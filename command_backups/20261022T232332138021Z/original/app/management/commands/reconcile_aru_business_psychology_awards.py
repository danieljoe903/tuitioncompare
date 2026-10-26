import copy
import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    DepositPolicy,
    ScholarshipRule,
    Tuition,
    TuitionSource,
    TuitionUpdateAudit,
)

from app.management.commands.preview_aru_business_psychology_fees import (
    Command as FeeEvidenceValidator,
    TARGETS,
)


YEAR = "2027/28"

RULE_SCOPES = {
    (
        "ARU International Undergraduate Scholarship "
        "— Lower — Year 1 (2027/28)"
    ): {"C_UK003_05"},
    (
        "ARU International Undergraduate Scholarship "
        "— Higher — Year 1 (2027/28)"
    ): {"C_UK003_05"},
    (
        "ARU International Postgraduate Scholarship "
        "— ARU graduate eligibility (2027/28)"
    ): {"C_UK003_30"},
}


class Command(BaseCommand):
    help = (
        "Extend saved ARU scholarship/deposit scopes to the "
        "reviewed Business and Psychology routes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--evidence", required=True)

    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        path = Path(options["evidence"])
        if not path.is_absolute():
            path = root / path

        try:
            evidence = json.loads(
                path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError) as exc:
            raise CommandError(
                f"Cannot read evidence: {exc}"
            ) from exc

        if (
            not isinstance(evidence, dict)
            or evidence.get("university_id") != "UK003"
            or evidence.get("errors")
            or not isinstance(evidence.get("pages"), list)
        ):
            raise CommandError("Invalid course evidence.")

        validator = FeeEvidenceValidator()
        pages = {}

        # Reuse the validated route and fee extraction.
        for course_id, target in TARGETS.items():
            try:
                page = validator.validated_page(
                    path.parent,
                    evidence["pages"],
                    target,
                )
            except (OSError, ValueError) as exc:
                raise CommandError(
                    f"{course_id}: invalid source evidence: {exc}"
                ) from exc

            if (
                "deposit of £4,000 or a sponsorship letter"
                not in page.get("text", "")
            ):
                raise CommandError(
                    f"{course_id}: deposit evidence not found."
                )

            pages[course_id] = page

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "evidence_report": str(path),
            "scholarship_rules": [],
            "deposit_policy": None,
            "courses": [],
        }

        folder = root / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / (
            "business-psychology-awards-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        with transaction.atomic():
            courses = {
                course.course_id: course
                for course in (
                    Course.objects.select_for_update()
                    .filter(
                        university_id="UK003",
                        course_id__in=TARGETS,
                        is_active=True,
                    )
                    .order_by("course_id")
                )
            }

            if set(courses) != set(TARGETS):
                raise CommandError(
                    "Both active reviewed courses are required."
                )

            tuitions = list(
                Tuition.objects.select_for_update()
                .filter(
                    course_id__in=TARGETS,
                    residency="INTL",
                )
                .order_by("pk")
            )

            tuition_map = {}
            for tuition in tuitions:
                if tuition.course_id in tuition_map:
                    raise CommandError(
                        "Duplicate international tuition found."
                    )
                tuition_map[tuition.course_id] = tuition

            if set(tuition_map) != set(TARGETS):
                raise CommandError(
                    "Both international tuition records are required."
                )

            sources = {
                source.tuition_id: source
                for source in (
                    TuitionSource.objects.select_for_update()
                    .filter(tuition__in=tuitions)
                    .order_by("pk")
                )
            }

            source_plans = []

            for course_id, target in TARGETS.items():
                course = courses[course_id]
                tuition = tuition_map[course_id]
                source = sources.get(tuition.pk)

                if (
                    course.title != target["title"]
                    or course.location != target["location"]
                    or course.level != target["level"]
                    or course.duration != target["duration"]
                    or course.start_month != "September"
                    or course.start_year != 2027
                    or course.work_placement
                    or tuition.currency != "GBP"
                    or Decimal(str(tuition.tuition_fee))
                    != Decimal(target["amount"])
                    or source is None
                    or source.academic_year != YEAR
                    or source.fee_basis != "annual_tuition"
                    or source.source_url.rstrip("/")
                    != target["url"]
                    or not isinstance(source.raw_fees, dict)
                    or source.raw_fees.get("verified") is not True
                ):
                    raise CommandError(
                        f"{course_id}: apply the reviewed fee "
                        "and course correction first."
                    )

                old = {
                    "raw_fees": copy.deepcopy(source.raw_fees),
                    "scholarship": str(tuition.scholarship),
                    "scholarship_source_id": (
                        tuition.scholarship_source_id
                    ),
                }

                new = copy.deepcopy(old)
                raw = new["raw_fees"]
                raw["scholarship_status"] = (
                    "verified_rule_eligibility_required"
                )
                raw["deposit_status"] = (
                    "verified_policy_eligibility_required"
                )
                raw["scholarship_scope_note"] = (
                    "Existing first-year UG lower/higher rules; "
                    "ARU must confirm the applicable award tier."
                    if target["level"] == "UG"
                    else
                    "Existing first-year International PG rule "
                    "for eligible ARU graduates. The award is "
                    "a conditional maximum, not guaranteed."
                )
                raw["published_deposit_evidence"] = {
                    "amount": "4000.00",
                    "currency": "GBP",
                    "alternative": "sponsorship_letter",
                    "source_url": target["url"],
                    "source_sha256": pages[course_id]["sha256"],
                }

                # Remove legacy numeric deductions so that the
                # saved eligibility rules govern the calculation.
                new["scholarship"] = "0.00"
                new["scholarship_source_id"] = None

                comparable_old = copy.deepcopy(old)
                comparable_old["scholarship"] = (
                    f"{Decimal(str(tuition.scholarship)):.2f}"
                )
                changed = comparable_old != new

                source_plans.append(
                    (source, tuition, new, changed)
                )
                report["courses"].append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": old,
                    "after": new,
                })

            rule_plans = []

            for name, additions in RULE_SCOPES.items():
                matches = list(
                    ScholarshipRule.objects.select_for_update()
                    .filter(
                        scholarship__university_id="UK003",
                        scholarship__name=name,
                        verified=True,
                    )
                )

                if len(matches) != 1:
                    raise CommandError(
                        f"Expected one verified rule: {name}"
                    )

                rule = matches[0]
                old = copy.deepcopy(rule.rules)

                if (
                    not isinstance(old, dict)
                    or old.get("academic_year") != YEAR
                    or old.get("study_years") != [1]
                    or not isinstance(old.get("conditions"), dict)
                ):
                    raise CommandError(
                        f"Unsupported saved rule: {name}"
                    )

                conditions = old["conditions"]
                scope = conditions.get("course_id")

                if (
                    not isinstance(scope, list)
                    or not all(isinstance(v, str) for v in scope)
                ):
                    raise CommandError(
                        f"Invalid saved course scope: {name}"
                    )

                is_pg = "Postgraduate" in name

                if (
                    old.get("kind")
                    != ("fixed_maximum" if is_pg else "fixed")
                    or (
                        is_pg
                        and conditions.get("aru_graduate") != [True]
                    )
                ):
                    raise CommandError(
                        f"Unexpected eligibility structure: {name}"
                    )

                new = copy.deepcopy(old)
                new["conditions"]["course_id"] = sorted(
                    set(scope) | additions
                )

                rule_plans.append((rule, new, old != new))
                report["scholarship_rules"].append({
                    "name": name,
                    "changed": old != new,
                    "before": old,
                    "after": new,
                })

            policies = list(
                DepositPolicy.objects.select_for_update().filter(
                    university_id="UK003",
                    academic_year=YEAR,
                    currency="GBP",
                    verified=True,
                )
            )

            if len(policies) != 1:
                raise CommandError(
                    "Expected one verified ARU deposit policy."
                )

            policy = policies[0]
            old_policy = copy.deepcopy(policy.rules)

            if (
                not isinstance(old_policy, dict)
                or old_policy.get("schema_version") != 1
                or old_policy.get("kind") != "fixed"
                or old_policy.get("currency") != "GBP"
                or old_policy.get("fee_status") != "INTL"
                or old_policy.get("payment_stage") != "before_cas"
                or not isinstance(
                    old_policy.get("course_ids"), list
                )
                or not all(
                    isinstance(v, str)
                    for v in old_policy["course_ids"]
                )
            ):
                raise CommandError(
                    "Saved deposit policy requires review."
                )

            try:
                amount = Decimal(str(old_policy["amount"]))
                if not amount.is_finite() or amount != Decimal("4000"):
                    raise ValueError
            except Exception as exc:
                raise CommandError(
                    "Expected the saved GBP 4,000 deposit."
                ) from exc

            new_policy = copy.deepcopy(old_policy)
            new_policy["course_ids"] = sorted(
                set(old_policy["course_ids"]) | set(TARGETS)
            )

            source_urls = new_policy.setdefault(
                "course_source_urls", {}
            )
            if not isinstance(source_urls, dict):
                raise CommandError(
                    "Deposit source map requires review."
                )

            for course_id, target in TARGETS.items():
                source_urls[course_id] = target["url"]

            policy_changed = old_policy != new_policy
            report["deposit_policy"] = {
                "changed": policy_changed,
                "before": old_policy,
                "after": new_policy,
            }
            report["proposed_items"] = (
                sum(plan[3] for plan in source_plans)
                + sum(plan[2] for plan in rule_plans)
                + int(policy_changed)
            )

            output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            if options["apply"]:
                for source, tuition, new, changed in source_plans:
                    if not changed:
                        continue

                    source.raw_fees = new["raw_fees"]
                    source.save(update_fields=["raw_fees"])

                    tuition.scholarship = Decimal("0.00")
                    tuition.scholarship_source_id = None
                    tuition.save(update_fields=[
                        "scholarship",
                        "scholarship_source",
                    ])

                    entry = next(
                        item
                        for item in report["courses"]
                        if item["course_id"] == tuition.course_id
                    )
                    TuitionUpdateAudit.objects.create(
                        tuition=tuition,
                        course_identifier=tuition.course_id,
                        residency="INTL",
                        before=entry["before"],
                        after=entry["after"],
                    )

                for rule, new, changed in rule_plans:
                    if changed:
                        rule.rules = new
                        rule.save(update_fields=["rules"])

                if policy_changed:
                    policy.rules = new_policy
                    policy.save(
                        update_fields=["rules", "updated_at"]
                    )

        report["database_changes"] = bool(
            options["apply"] and report["proposed_items"]
        )
        report["completed"] = True

        output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        for entry in report["scholarship_rules"]:
            state = "update" if entry["changed"] else "unchanged"
            self.stdout.write(
                f"{entry['name']}: "
                f"{len(entry['after']['conditions']['course_id'])} "
                f"courses — {state}"
            )

        self.stdout.write(
            f"Deposit scope: {len(new_policy['course_ids'])} courses"
        )
        self.stdout.write(
            f"Proposed/updated items: {report['proposed_items']}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Scholarship conditions and frontend questions preserved."
        )
        self.stdout.write(
            "Gross tuition and numeric stored deposits unchanged."
        )

        if options["apply"]:
            self.stdout.write(
                "Scholarship scopes, deposit scope and metadata saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )
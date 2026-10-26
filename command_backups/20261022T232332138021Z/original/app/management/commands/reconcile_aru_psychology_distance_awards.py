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

from app.management.commands.reconcile_aru_psychology_distance_routes import (
    Command as RouteValidator,
    TARGETS,
)


UG_ID = "C_UK003_31"
RESEARCH_ID = "C_ARU003_RES_04"
DISTANCE_ID = "C_UK003_52"

RULE_NAMES = [
    (
        "ARU International Undergraduate Scholarship "
        "— Lower — Year 1 (2027/28)"
    ),
    (
        "ARU International Undergraduate Scholarship "
        "— Higher — Year 1 (2027/28)"
    ),
]


class Command(BaseCommand):
    help = (
        "Reconcile saved award/deposit scopes for the "
        "Psychology and distance-learning batch."
    )

    def add_arguments(self, parser):
        parser.add_argument("--evidence", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        path = Path(options["evidence"])
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path

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

        validator = RouteValidator()
        try:
            page = validator.validate_page(
                path.parent,
                evidence["pages"],
                TARGETS[UG_ID],
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise CommandError(
                f"Undergraduate evidence validation failed: {exc}"
            ) from exc

        if (
            "deposit of £4,000 or a sponsorship letter"
            not in page.get("text", "")
        ):
            raise CommandError(
                "Undergraduate Psychology deposit evidence missing."
            )

        folder = Path(settings.BASE_DIR) / "logs" / "aru"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / (
            "psychology-distance-awards-"
            + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            + ".json"
        )

        report = {
            "generated_at": timezone.now().isoformat(),
            "mode": "apply" if options["apply"] else "preview",
            "database_changes": False,
            "evidence_report": str(path),
            "courses": [],
            "scholarship_rules": [],
            "deposit_policy": None,
        }

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
                    "Expected all three active reviewed courses."
                )

            tuitions = list(
                Tuition.objects.select_for_update()
                .filter(course_id__in=TARGETS, residency="INTL")
                .order_by("pk")
            )
            tuition_map = {}

            for tuition in tuitions:
                if tuition.course_id in tuition_map:
                    raise CommandError(
                        "Duplicate international tuition found."
                    )
                tuition_map[tuition.course_id] = tuition

            if (
                set(tuition_map) != set(TARGETS)
                or any(t.currency != "GBP" for t in tuitions)
            ):
                raise CommandError(
                    "Each course requires one GBP international tuition."
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

                expected_url = (
                    "https://www.aru.ac.uk/study/" + target["slug"]
                )

                if (
                    course.title != target["title"]
                    or course.level != target["level"]
                    or course.location != target["location"]
                    or course.start_month != target["month"]
                    or course.start_year != 2027
                    or source is None
                    or source.source_url.rstrip("/") != expected_url
                    or source.academic_year != target["academic_year"]
                    or not isinstance(source.raw_fees, dict)
                ):
                    raise CommandError(
                        f"{course_id}: apply the reviewed route "
                        "and fee import first."
                    )

                verified = target["amount"] is not None
                if source.raw_fees.get("verified") is not verified:
                    raise CommandError(
                        f"{course_id}: unexpected fee verification status."
                    )

                if verified and (
                    Decimal(str(tuition.tuition_fee))
                    != Decimal(target["amount"])
                ):
                    raise CommandError(
                        f"{course_id}: unexpected stored tuition."
                    )

                before = {
                    "raw_fees": copy.deepcopy(source.raw_fees),
                    "scholarship": (
                        f"{Decimal(str(tuition.scholarship)):.2f}"
                    ),
                    "scholarship_source_id": (
                        tuition.scholarship_source_id
                    ),
                }
                after = copy.deepcopy(before)
                raw = after["raw_fees"]

                if course_id == UG_ID:
                    raw["scholarship_status"] = (
                        "verified_rule_eligibility_required"
                    )
                    raw["deposit_status"] = (
                        "verified_policy_eligibility_required"
                    )
                    raw["award_review_note"] = (
                        "Saved first-year undergraduate lower/higher "
                        "rules apply conditionally. ARU must confirm "
                        "the award tier and individual eligibility."
                    )
                    raw["published_deposit_evidence"] = {
                        "amount": "4000.00",
                        "currency": "GBP",
                        "alternative": "sponsorship_letter",
                        "source_url": page["source_url"],
                        "source_sha256": page["sha256"],
                    }

                elif course_id == RESEARCH_ID:
                    raw["scholarship_status"] = (
                        "research_applicability_unconfirmed"
                    )
                    raw["deposit_status"] = (
                        "research_deposit_unconfirmed"
                    )
                    raw["award_review_note"] = (
                        "No applicable MPhil Psychology scholarship "
                        "or research deposit amount has been verified "
                        "for January 2027. Confirm with ARU."
                    )

                else:
                    raw["scholarship_status"] = (
                        "distance_learning_applicability_unconfirmed"
                    )
                    raw["deposit_status"] = (
                        "campus_pre_cas_policy_not_applicable"
                    )
                    raw["award_review_note"] = (
                        "Distance-learning scholarship applicability "
                        "remains unconfirmed. The campus pre-CAS "
                        "deposit rule does not apply; any separate "
                        "online-course payment terms require confirmation."
                    )

                # The comparison should use reviewed eligibility
                # rules rather than an unverified legacy deduction.
                after["scholarship"] = "0.00"
                after["scholarship_source_id"] = None

                changed = before != after
                source_plans.append(
                    (source, tuition, after, changed)
                )
                report["courses"].append({
                    "course_id": course_id,
                    "changed": changed,
                    "before": before,
                    "after": after,
                })

            rule_plans = []

            for name in RULE_NAMES:
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
                    or old.get("academic_year") != "2027/28"
                    or old.get("kind") != "fixed"
                    or old.get("study_years") != [1]
                    or not isinstance(old.get("conditions"), dict)
                ):
                    raise CommandError(
                        f"Unsupported saved rule: {name}"
                    )

                scope = old["conditions"].get("course_id")
                if (
                    not isinstance(scope, list)
                    or not all(isinstance(v, str) for v in scope)
                    or set(scope) & {RESEARCH_ID, DISTANCE_ID}
                ):
                    raise CommandError(
                        f"Unexpected course scope: {name}"
                    )

                new = copy.deepcopy(old)
                new["conditions"]["course_id"] = sorted(
                    set(scope) | {UG_ID}
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
                    academic_year="2027/28",
                    currency="GBP",
                    verified=True,
                )
            )
            if len(policies) != 1:
                raise CommandError(
                    "Expected one verified ARU deposit policy."
                )

            policy = policies[0]
            old = copy.deepcopy(policy.rules)

            if (
                not isinstance(old, dict)
                or old.get("schema_version") != 1
                or old.get("kind") != "fixed"
                or old.get("currency") != "GBP"
                or old.get("fee_status") != "INTL"
                or old.get("payment_stage") != "before_cas"
                or not isinstance(old.get("course_ids"), list)
                or not all(
                    isinstance(v, str) for v in old["course_ids"]
                )
            ):
                raise CommandError(
                    "Saved deposit policy requires review."
                )

            try:
                amount = Decimal(str(old["amount"]))
                if not amount.is_finite() or amount != Decimal("4000"):
                    raise ValueError
            except Exception as exc:
                raise CommandError(
                    "Expected the saved GBP 4,000 deposit."
                ) from exc

            if set(old["course_ids"]) & {RESEARCH_ID, DISTANCE_ID}:
                raise CommandError(
                    "Research or distance-learning course is "
                    "already in the campus deposit scope; review required."
                )

            new = copy.deepcopy(old)
            new["course_ids"] = sorted(
                set(old["course_ids"]) | {UG_ID}
            )

            source_urls = new.setdefault("course_source_urls", {})
            if not isinstance(source_urls, dict):
                raise CommandError(
                    "Deposit source map requires review."
                )
            source_urls[UG_ID] = page["source_url"]

            policy_changed = old != new
            report["deposit_policy"] = {
                "changed": policy_changed,
                "before": old,
                "after": new,
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
                for source, tuition, after, changed in source_plans:
                    if not changed:
                        continue

                    source.raw_fees = after["raw_fees"]
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
                        after=after,
                    )

                for rule, rules, changed in rule_plans:
                    if changed:
                        rule.rules = rules
                        rule.save(update_fields=["rules"])

                if policy_changed:
                    policy.rules = new
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
            f"Deposit scope: {len(new['course_ids'])} courses"
        )
        self.stdout.write(
            "MPhil Psychology: scholarship and deposit pending."
        )
        self.stdout.write(
            "Distance-learning MSc: scholarship pending; "
            "campus pre-CAS policy not applicable."
        )
        self.stdout.write(
            f"Proposed/updated items: {report['proposed_items']}"
        )
        self.stdout.write(f"Report: {output}")
        self.stdout.write(
            "Eligibility questions preserved. Gross tuition "
            "and numeric stored deposits unchanged."
        )
        self.stdout.write(
            "Award/deposit reconciliation saved."
            if options["apply"]
            else "Preview only. No database records changed."
        )
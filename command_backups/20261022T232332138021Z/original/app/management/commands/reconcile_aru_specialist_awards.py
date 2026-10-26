import copy
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
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

from .preview_aru_specialist_routes import (
    Command as RouteValidator,
    TARGETS,
)


YEAR = "2027/28"

SCHOLARSHIP_URL = (
    "https://www.aru.ac.uk/international/"
    "international-scholarships/"
    "international-undergraduate-scholarship"
)

DEPOSIT_URL = (
    "https://www.aru.ac.uk/international/how-to-apply/"
    "step-4---receive-our-decision-on-your-application"
)

IDS = set(TARGETS)

DEPOSIT_IDS = {
    "C_ARU003_01",
    "C_UK003_59",
    "C_UK003_70",
}

LOWER_IDS = {
    "C_UK003_59",
    "C_UK003_70",
}

HIGHER_IDS = {
    "C_UK003_59",
}

NOTES = {
    "C_ARU003_01": (
        "The top-up route requires confirmation of course entry year "
        "and Accredited Prior Certified Learning. Do not automatically "
        "apply a Year 1 scholarship or assume a fixed later-entry award."
    ),
    "C_UK003_59": (
        "Reviewed undergraduate-entry MOptom route. Lower or higher "
        "first-year scholarship remains conditional on ARU's assessment "
        "and all saved eligibility conditions. No postgraduate award "
        "is carried over from the replaced MSc record."
    ),
    "C_UK003_70": (
        "Reviewed standard three-year Equine Science route at Writtle. "
        "Included in the lower first-year scholarship scope only. "
        "Registration, entry-year, APCL and funding conditions still apply."
    ),
    "C_UK003_87": (
        "The accelerated blended-learning route needs separate "
        "international fee, scholarship and visa/payment confirmation. "
        "No automatic scholarship deduction or deposit estimate."
    ),
    "C_UK003_88": (
        "International application eligibility and fee status require "
        "confirmation for the blended Social Work route. No automatic "
        "scholarship deduction or deposit estimate."
    ),
    "C_UK003_104": (
        "January 2027 entry needs applicable 2026/27 evidence. "
        "Do not apply 2027/28 scholarship or deposit scope to this intake."
    ),
}

EXTRA_QUESTIONS = [
    {
        "key": "entry_year",
        "type": "integer",
        "question": "Which course year will you enter?",
    },
    {
        "key": "apcl",
        "type": "boolean",
        "question": (
            "Has ARU granted you Accredited Prior Certified Learning?"
        ),
    },
    {
        "key": "aru_course_award_applicability_confirmed",
        "type": "boolean",
        "question": (
            "Has ARU confirmed that this scholarship covers "
            "your exact course, study mode and intake?"
        ),
    },
    {
        "key": "aru_international_route_eligibility_confirmed",
        "type": "boolean",
        "question": (
            "Has ARU confirmed your eligibility to join this route "
            "as an international student?"
        ),
    },
]


def normal(value):
    return " ".join(str(value).split()).casefold()


def merge_questions(*groups):
    result = {}

    for group in groups:
        for question in group or []:
            if isinstance(question, dict) and question.get("key"):
                result.setdefault(
                    question["key"],
                    copy.deepcopy(question),
                )

    return list(result.values())


class Command(BaseCommand):
    help = (
        "Reconcile scholarship applicability and deposit scope "
        "for six reviewed ARU specialist routes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--evidence", required=True)
        parser.add_argument("--apply", action="store_true")

    def fetch_sources(self):
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        collected = {}

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionComparePolicyReview/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )

            for label, url in {
                "scholarship": SCHOLARSHIP_URL,
                "deposit": DEPOSIT_URL,
            }.items():
                self.stdout.write(f"Reading: {url}")

                response = session.get(url, timeout=(30, 60))
                response.raise_for_status()

                if response.url.rstrip("/") != url.rstrip("/"):
                    raise ValueError(
                        f"Unexpected redirect: {response.url}"
                    )

                soup = BeautifulSoup(response.content, "html.parser")

                if soup.find("h1") is None:
                    raise ValueError(f"No page title: {url}")

                main = soup.find("main") or soup

                text = " ".join(
                    main.get_text(" ", strip=True).split()
                )

                collected[label] = {
                    "source_url": response.url,
                    "text": text,
                    "text_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    "html_sha256": hashlib.sha256(
                        response.content
                    ).hexdigest(),
                }

        scholarship = normal(collected["scholarship"]["text"])

        for marker in [
            "£2,000",
            "£4,000",
            "writtle",
            "year 2",
            "year 3",
            "apcl",
            "registration",
            "distance learning",
        ]:
            if marker not in scholarship:
                raise ValueError(
                    f"Scholarship evidence changed: missing {marker!r}"
                )

        deposit = collected["deposit"]["text"]

        if not re.search(
            r"deposit for Undergraduate and Postgraduate taught "
            r"courses is\s*£\s*4,000",
            deposit,
            re.IGNORECASE,
        ):
            raise ValueError(
                "Cannot verify the current £4,000 taught-course deposit."
            )

        if "before being issued with a cas" not in normal(deposit):
            raise ValueError("Cannot verify the pre-CAS payment stage.")

        return collected

    def build_plan(self, evidence, evidence_path, sources, lock=False):
        validator = RouteValidator()

        courses = Course.objects.filter(
            university_id="UK003",
            course_id__in=IDS,
        ).order_by("course_id")

        tuitions = Tuition.objects.filter(
            course__university_id="UK003",
            course_id__in=IDS,
            residency="INTL",
        ).order_by("course_id")

        source_rows = TuitionSource.objects.filter(
            tuition__course__university_id="UK003",
            tuition__course_id__in=IDS,
            tuition__residency="INTL",
        ).order_by("tuition_id")

        rule_rows = ScholarshipRule.objects.filter(
            scholarship__university_id="UK003",
        ).order_by("pk")

        policies = DepositPolicy.objects.filter(
            university_id="UK003",
            academic_year=YEAR,
            currency="GBP",
        )

        if lock:
            courses = courses.select_for_update()
            tuitions = tuitions.select_for_update()
            source_rows = source_rows.select_for_update()
            rule_rows = rule_rows.select_for_update()
            policies = policies.select_for_update()

        course_map = {row.course_id: row for row in courses}
        tuition_rows = list(tuitions)
        source_map = {
            row.tuition_id: row for row in source_rows
        }
        rules = list(rule_rows)
        policy_rows = list(policies)

        if set(course_map) != IDS:
            raise ValueError("One or more reviewed courses are missing.")

        if len(policy_rows) != 1:
            raise ValueError(
                "Exactly one saved 2027/28 GBP deposit policy is required."
            )

        policy = policy_rows[0]
        deposit_rules = copy.deepcopy(policy.rules)

        if (
            not policy.verified
            or not isinstance(deposit_rules, dict)
            or deposit_rules.get("kind") != "fixed"
            or Decimal(str(deposit_rules.get("amount"))) != Decimal("4000")
            or deposit_rules.get("academic_year") != YEAR
            or deposit_rules.get("fee_status") != "INTL"
            or deposit_rules.get("payment_stage") != "before_cas"
            or deposit_rules.get("requires_student_visa") is not True
            or deposit_rules.get("full_fee_paying") is not True
            or deposit_rules.get(
                "deduct_scholarship_from_deposit"
            ) is not False
            or not isinstance(deposit_rules.get("course_ids"), list)
            or not isinstance(
                deposit_rules.get("eligibility_questions"), list
            )
        ):
            raise ValueError(
                "Saved deposit policy does not match the reviewed rule."
            )

        chosen = {}

        for tier, amount in [
            ("lower", Decimal("2000")),
            ("higher", Decimal("4000")),
        ]:
            candidates = []

            for rule in rules:
                data = rule.rules

                if not isinstance(data, dict):
                    continue

                conditions = data.get("conditions", {})

                if (
                    rule.verified
                    and rule.source_url.rstrip("/")
                    == SCHOLARSHIP_URL.rstrip("/")
                    and data.get("academic_year") == YEAR
                    and data.get("kind") == "fixed"
                    and data.get("currency") == "GBP"
                    and str(data.get("value")) in {
                        str(amount),
                        f"{amount:.2f}",
                        str(int(amount)),
                    }
                    and conditions.get("entry_year") == [1]
                    and conditions.get("fee_status") == ["INTL"]
                    and conditions.get("aru_ug_award_tier") == [tier]
                    and data.get("study_years") == [1]
                    and data.get("award_status") == "conditional"
                    and isinstance(
                        conditions.get("course_id"), list
                    )
                    and data.get("eligibility_questions")
                ):
                    candidates.append(rule)

            if len(candidates) != 1:
                raise ValueError(
                    f"Expected one verified Year 1 {tier} UG rule."
                )

            chosen[tier] = candidates[0]

        rule_changes = []

        # Remove these reused course IDs from incompatible old scopes.
        # Only the two reviewed undergraduate scopes receive additions.
        for rule in rules:
            old = rule.rules

            if not isinstance(old, dict):
                continue

            conditions = old.get("conditions")

            if not isinstance(conditions, dict):
                continue

            scope = conditions.get("course_id")

            if not isinstance(scope, list):
                continue

            new = copy.deepcopy(old)
            new_scope = set(scope) - IDS

            if rule.pk == chosen["lower"].pk:
                new_scope |= LOWER_IDS

            if rule.pk == chosen["higher"].pk:
                new_scope |= HIGHER_IDS

            new["conditions"]["course_id"] = sorted(new_scope)

            if new != old:
                rule_changes.append({
                    "id": rule.pk,
                    "before": old,
                    "after": new,
                })

        old_scope = set(deposit_rules["course_ids"])
        deposit_rules["course_ids"] = sorted(
            (old_scope - IDS) | DEPOSIT_IDS
        )

        kinds = deposit_rules.get("course_kinds")

        if not isinstance(kinds, list):
            raise ValueError("Deposit course kinds require review.")

        deposit_rules["course_kinds"] = sorted(
            set(kinds)
            | {"undergraduate_taught", "undergraduate_topup"}
        )

        course_changes = []

        base_questions = chosen["lower"].rules["eligibility_questions"]

        for course_id in sorted(IDS):
            proposal = validator.make_proposal(
                evidence,
                evidence_path,
                course_id,
                TARGETS[course_id],
            )

            if proposal["course_changes"]:
                raise ValueError(
                    f"{course_id}: apply the route corrections first."
                )

            matches = [
                row for row in tuition_rows
                if row.course_id == course_id
            ]

            if len(matches) != 1 or matches[0].currency != "GBP":
                raise ValueError(
                    f"{course_id}: one GBP international tuition required."
                )

            tuition = matches[0]
            source = source_map.get(tuition.pk)

            if source is None or not isinstance(source.raw_fees, dict):
                raise ValueError(
                    f"{course_id}: reviewed source metadata is missing."
                )

            raw = copy.deepcopy(source.raw_fees)
            target_fee = TARGETS[course_id]["fee"]

            if target_fee is not None:
                if (
                    raw.get("verified") is not True
                    or source.academic_year != YEAR
                    or Decimal(str(raw.get("annual_tuition")))
                    != Decimal(target_fee)
                    or Decimal(str(tuition.tuition_fee))
                    != Decimal(target_fee)
                ):
                    raise ValueError(
                        f"{course_id}: verified tuition does not match."
                    )
            elif raw.get("verified") is not False:
                raise ValueError(
                    f"{course_id}: expected an unavailable-fee outcome."
                )

            if (
                source.source_url.rstrip("/")
                != proposal["evidence"]["source_url"].rstrip("/")
            ):
                raise ValueError(
                    f"{course_id}: saved source URL differs."
                )

            existing_questions = (
                raw.get("scholarship_review", {})
                .get("eligibility_questions", [])
            )

            if course_id in LOWER_IDS:
                scholarship_status = (
                    "verified_rule_eligibility_required"
                )
                tiers = (
                    ["lower", "higher"]
                    if course_id in HIGHER_IDS
                    else ["lower"]
                )
            else:
                scholarship_status = "applicability_unconfirmed"
                tiers = []

            deposit_status = (
                "verified_rule_eligibility_required"
                if course_id in DEPOSIT_IDS
                else "applicability_pending"
            )

            raw.update({
                "course_kind": (
                    "undergraduate_topup"
                    if course_id == "C_ARU003_01"
                    else (
                        "postgraduate_taught"
                        if course_id == "C_UK003_104"
                        else "undergraduate_taught"
                    )
                ),
                "scholarship_status": scholarship_status,
                "scholarship_review": {
                    "status": scholarship_status,
                    "note": NOTES[course_id],
                    "academic_year": (
                        "2026/27"
                        if course_id == "C_UK003_104"
                        else YEAR
                    ),
                    "eligible_rule_tiers": tiers,
                    "source_url": SCHOLARSHIP_URL,
                    "policy_text_sha256": (
                        sources["scholarship"]["text_sha256"]
                    ),
                    "source_period": "current_undated_policy",
                    "eligibility_questions": merge_questions(
                        existing_questions,
                        base_questions,
                        EXTRA_QUESTIONS,
                    ),
                },
                "deposit_status": deposit_status,
                "deposit_review": {
                    "status": deposit_status,
                    "note": (
                        "Saved fixed pre-CAS policy applies subject "
                        "to visa, full-fee-paying and sponsorship "
                        "answers. Confirm the individual offer."
                        if course_id in DEPOSIT_IDS
                        else NOTES[course_id]
                    ),
                    "policy_academic_year": (
                        YEAR if course_id in DEPOSIT_IDS else None
                    ),
                    "source_url": DEPOSIT_URL,
                    "policy_text_sha256": (
                        sources["deposit"]["text_sha256"]
                    ),
                    "eligibility_questions": copy.deepcopy(
                        deposit_rules["eligibility_questions"]
                    ),
                },
            })

            before = {
                "raw_fees": source.raw_fees,
                "scholarship": str(tuition.scholarship),
                "scholarship_source_id": (
                    tuition.scholarship_source_id
                ),
            }
            after = {
                "raw_fees": raw,
                "scholarship": str(
                    Decimal("0.00")
                    if tuition.scholarship != 0
                    else tuition.scholarship
                ),
                "scholarship_source_id": None,
            }

            if before != after:
                course_changes.append({
                    "course_id": course_id,
                    "tuition_id": tuition.pk,
                    "source_id": source.pk,
                    "before": before,
                    "after": after,
                })

        return {
            "rule_changes": rule_changes,
            "deposit_policy_id": policy.pk,
            "deposit_before": policy.rules,
            "deposit_after": deposit_rules,
            "course_changes": course_changes,
            "deposit_scope_count": len(
                deposit_rules["course_ids"]
            ),
        }

    def save_plan(self, plan):
        for change in plan["rule_changes"]:
            rule = ScholarshipRule.objects.get(pk=change["id"])
            rule.rules = change["after"]
            rule.reviewed_at = timezone.now()
            rule.save(update_fields=["rules", "reviewed_at"])

        if plan["deposit_before"] != plan["deposit_after"]:
            policy = DepositPolicy.objects.get(
                pk=plan["deposit_policy_id"]
            )
            policy.rules = plan["deposit_after"]
            policy.save(update_fields=["rules", "updated_at"])

        for change in plan["course_changes"]:
            tuition = Tuition.objects.get(pk=change["tuition_id"])
            source = TuitionSource.objects.get(pk=change["source_id"])

            source.raw_fees = change["after"]["raw_fees"]
            source.save(update_fields=["raw_fees"])

            if (
                tuition.scholarship != 0
                or tuition.scholarship_source_id is not None
            ):
                tuition.scholarship = Decimal("0.00")
                tuition.scholarship_source = None
                tuition.save(update_fields=[
                    "scholarship",
                    "scholarship_source",
                ])

            TuitionUpdateAudit.objects.create(
                tuition=tuition,
                course_identifier=change["course_id"],
                residency="INTL",
                before=change["before"],
                after=change["after"],
            )

    def handle(self, *args, **options):
        evidence_path = Path(options["evidence"])

        if not evidence_path.is_absolute():
            evidence_path = Path(settings.BASE_DIR) / evidence_path

        try:
            evidence = json.loads(
                evidence_path.read_text(encoding="utf-8-sig")
            )

            if evidence.get("university_id") != "UK003":
                raise ValueError("Evidence is not for ARU UK003.")

            sources = self.fetch_sources()

            with transaction.atomic():
                plan = self.build_plan(
                    evidence,
                    evidence_path,
                    sources,
                    lock=options["apply"],
                )

                stamp = timezone.now().strftime(
                    "%Y%m%dT%H%M%S%fZ"
                )
                folder = (
                    Path(settings.BASE_DIR)
                    / "logs"
                    / "aru"
                    / f"specialist-awards-{stamp}"
                )
                folder.mkdir(parents=True, exist_ok=True)

                output = folder / "reconciliation.json"

                report = {
                    "generated_at": timezone.now().isoformat(),
                    "mode": (
                        "apply_requested"
                        if options["apply"]
                        else "preview"
                    ),
                    "database_changes": False,
                    "university_id": "UK003",
                    "evidence_report": str(evidence_path),
                    "policy_sources": sources,
                    "plan": plan,
                }

                output.write_text(
                    json.dumps(
                        report,
                        cls=DjangoJSONEncoder,
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                if options["apply"]:
                    self.save_plan(plan)

        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            requests.RequestException,
        ) as exc:
            raise CommandError(
                f"Reconciliation stopped: {exc}. "
                "No database records changed."
            ) from exc

        changed = (
            len(plan["rule_changes"])
            + len(plan["course_changes"])
            + int(
                plan["deposit_before"] != plan["deposit_after"]
            )
        )

        if options["apply"]:
            report["mode"] = "apply"
            report["database_changes"] = changed > 0

            output.write_text(
                json.dumps(
                    report,
                    cls=DjangoJSONEncoder,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

        self.stdout.write(
            "MOptom: lower/higher UG rules; eligibility required."
        )
        self.stdout.write(
            "Equine Science: lower UG rule; eligibility required."
        )
        self.stdout.write(
            "Top-up: entry-year/APCL assessment pending."
        )
        self.stdout.write(
            "Early Childhood, Social Work and Global Public Health: "
            "scholarship/deposit applicability pending."
        )
        self.stdout.write(
            f"2027/28 deposit scope: "
            f"{plan['deposit_scope_count']} courses"
        )
        self.stdout.write(
            f"Proposed/updated items: {changed}"
        )
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Scholarship scopes, deposit scope and review "
                "metadata saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Eligibility questions preserved. Gross tuition and "
            "numeric deposit fields unchanged. Unverified stored "
            "scholarship deductions are cleared on apply."
        )
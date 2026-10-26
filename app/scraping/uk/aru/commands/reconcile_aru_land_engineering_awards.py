import copy
import json
from decimal import Decimal
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.management.base import CommandError
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
    University,
)

from app.scraping.uk.aru.commands.preview_aru_land_engineering_routes import Command as RouteValidator, TARGETS

from app.scraping.uk.aru.commands.reconcile_aru_specialist_awards import Command as PolicyInspector, merge_questions


YEAR = "2027/28"

UG_ID = "C_UK003_02"
PG_IDS = {"C_UK003_54", "C_UK003_71"}
COURSE_IDS = {UG_ID, *PG_IDS}

UG_URL = (
    "https://www.aru.ac.uk/international/"
    "international-scholarships/"
    "international-undergraduate-scholarship"
)

PG_URL = (
    "https://www.aru.ac.uk/international/"
    "international-scholarships/"
    "international-postgraduate-scholarship"
)


class Command(PolicyInspector):
    help = (
        "Reconcile scholarship and deposit scopes for three "
        "reviewed ARU land/engineering replacements."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--evidence",
            help=(
                "Optional campus-intake evidence path. Otherwise "
                "locate the latest report for these three courses."
            ),
        )
        parser.add_argument("--apply", action="store_true")

    def fetch_policies(self):
        # Reuse the existing UG/deposit source verification.
        sources = self.fetch_sources()

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )

        self.stdout.write(f"Reading: {PG_URL}")

        with requests.Session() as session:
            session.headers["User-Agent"] = (
                "TuitionComparePolicyReview/1.0"
            )
            session.mount(
                "https://",
                HTTPAdapter(max_retries=retry),
            )
            response = session.get(PG_URL, timeout=(30, 60))
            response.raise_for_status()

        if response.url.rstrip("/") != PG_URL.rstrip("/"):
            raise ValueError(
                f"Unexpected postgraduate redirect: {response.url}"
            )

        soup = BeautifulSoup(response.content, "html.parser")

        if soup.find("h1") is None:
            raise ValueError("Postgraduate policy has no title.")

        text = " ".join(
            (soup.find("main") or soup)
            .get_text(" ", strip=True)
            .split()
        )

        lower = text.casefold()

        for marker in [
            "£4,000",
            "aru graduate",
            "mba",
            "minimum entry requirements",
            "registration",
        ]:
            if marker not in lower:
                raise ValueError(
                    f"Postgraduate scholarship terms changed: {marker}"
                )

        sources["postgraduate"] = {
            "source_url": response.url,
            "text": text,
        }

        return sources

    def find_rule(self, rules, *, level):
        matches = []

        for rule in rules:
            data = rule.rules

            if not isinstance(data, dict):
                continue

            conditions = data.get("conditions", {})

            if not isinstance(conditions, dict):
                continue

            common = (
                rule.verified
                and data.get("academic_year") == YEAR
                and data.get("currency") == "GBP"
                and data.get("benefit") == "tuition"
                and data.get("fee_basis_confirmed") is True
                and data.get("award_status") == "conditional"
                and data.get("study_years") == [1]
                and conditions.get("fee_status") == ["INTL"]
                and isinstance(conditions.get("course_id"), list)
                and isinstance(
                    data.get("eligibility_questions"), list
                )
                and bool(data.get("eligibility_questions"))
                and rule.scholarship.level == level
            )

            if not common:
                continue

            if level == "UG":
                suitable = (
                    rule.source_url.rstrip("/")
                    == UG_URL.rstrip("/")
                    and data.get("kind") == "fixed"
                    and Decimal(str(data.get("value"))) == Decimal("2000")
                    and conditions.get("entry_year") == [1]
                    and conditions.get("aru_ug_award_tier") == ["lower"]
                )
            else:
                suitable = (
                    rule.source_url.rstrip("/")
                    == PG_URL.rstrip("/")
                    and data.get("kind") == "fixed_maximum"
                    and Decimal(str(data.get("value"))) == Decimal("4000")
                    and conditions.get("aru_graduate") == [True]
                )

            if suitable:
                matches.append(rule)

        if len(matches) != 1:
            raise ValueError(
                f"Expected one verified {level} scholarship rule; "
                f"found {len(matches)}."
            )

        return matches[0]

    def build_plan(self, evidence, evidence_path, lock=False):
        validator = RouteValidator()

        courses = Course.objects.filter(
            university_id="UK003",
            course_id__in=COURSE_IDS,
        ).order_by("course_id")

        tuitions = Tuition.objects.filter(
            course__university_id="UK003",
            course_id__in=COURSE_IDS,
            residency="INTL",
        ).order_by("course_id")

        source_rows = TuitionSource.objects.filter(
            tuition__course__university_id="UK003",
            tuition__course_id__in=COURSE_IDS,
            tuition__residency="INTL",
        ).order_by("tuition_id")

        rule_rows = (
            ScholarshipRule.objects
            .filter(scholarship__university_id="UK003")
            .select_related("scholarship")
            .order_by("pk")
        )

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

        if set(course_map) != COURSE_IDS:
            raise ValueError("One or more replacement courses are missing.")

        ug_rule = self.find_rule(rules, level="UG")
        pg_rule = self.find_rule(rules, level="PG")

        rule_changes = []

        for rule, additions in [
            (ug_rule, {UG_ID}),
            (pg_rule, PG_IDS),
        ]:
            after = copy.deepcopy(rule.rules)
            existing_ids = set(after["conditions"]["course_id"])

            after["conditions"]["course_id"] = sorted(
                existing_ids | additions
            )

            if after != rule.rules:
                rule_changes.append({
                    "id": rule.pk,
                    "name": rule.scholarship.name,
                    "before": rule.rules,
                    "after": after,
                })

        if len(policy_rows) != 1:
            raise ValueError(
                "Expected one saved 2027/28 GBP deposit policy."
            )

        policy = policy_rows[0]
        deposit_after = copy.deepcopy(policy.rules)

        if (
            not policy.verified
            or not isinstance(deposit_after, dict)
            or deposit_after.get("kind") != "fixed"
            or Decimal(str(deposit_after.get("amount"))) != Decimal("4000")
            or deposit_after.get("academic_year") != YEAR
            or deposit_after.get("fee_status") != "INTL"
            or deposit_after.get("payment_stage") != "before_cas"
            or deposit_after.get("requires_student_visa") is not True
            or deposit_after.get("full_fee_paying") is not True
            or deposit_after.get(
                "deduct_scholarship_from_deposit"
            ) is not False
            or not isinstance(deposit_after.get("course_ids"), list)
            or not deposit_after.get("eligibility_questions")
        ):
            raise ValueError(
                "Saved deposit policy does not match the reviewed rule."
            )

        deposit_after["course_ids"] = sorted(
            set(deposit_after["course_ids"]) | COURSE_IDS
        )

        kinds = deposit_after.get("course_kinds")

        if not isinstance(kinds, list):
            raise ValueError("Deposit course-kind definitions are invalid.")

        deposit_after["course_kinds"] = sorted(
            set(kinds)
            | {"undergraduate_taught", "postgraduate_taught"}
        )

        course_changes = []

        for course_id in sorted(COURSE_IDS):
            target = TARGETS[course_id]

            page, _, _ = validator.load_page(
                evidence,
                evidence_path,
                course_id,
                target,
            )
            validator.validate_schema(page, target)

            proposal = validator.make_proposal(
                evidence,
                evidence_path,
                course_id,
                target,
            )

            if proposal["course_changes"]:
                raise ValueError(
                    f"{course_id}: apply the reviewed replacement first."
                )

            matches = [
                row for row in tuition_rows
                if row.course_id == course_id
            ]

            if len(matches) != 1:
                raise ValueError(
                    f"{course_id}: one international tuition is required."
                )

            tuition = matches[0]
            source = source_map.get(tuition.pk)

            if source is None or not isinstance(source.raw_fees, dict):
                raise ValueError(
                    f"{course_id}: source metadata is missing."
                )

            raw = copy.deepcopy(source.raw_fees)

            if (
                tuition.currency != "GBP"
                or source.academic_year != YEAR
                or source.fee_basis != "annual_tuition"
                or raw.get("verified") is not True
                or raw.get("academic_year") != YEAR
                or raw.get("source_fee_category") != "International"
                or raw.get("currency") != "GBP"
                or source.source_url.rstrip("/")
                != proposal["evidence"]["source_url"].rstrip("/")
                or Decimal(str(tuition.tuition_fee))
                != Decimal(target["fee"])
                or Decimal(str(raw.get("annual_tuition")))
                != Decimal(target["fee"])
            ):
                raise ValueError(
                    f"{course_id}: verified tuition requires review."
                )

            undergraduate = course_id == UG_ID
            selected_rule = ug_rule if undergraduate else pg_rule

            previous_review = raw.get("scholarship_review")
            if not isinstance(previous_review, dict):
                previous_review = {}

            previous_deposit = raw.get("deposit_review")
            if not isinstance(previous_deposit, dict):
                previous_deposit = {}

            scholarship_status = (
                "verified_rule_eligibility_required"
                if undergraduate
                else "verified_maximum_rule_eligibility_required"
            )

            raw.update({
                "scholarship_status": scholarship_status,
                "scholarship_review": {
                    **previous_review,
                    "status": scholarship_status,
                    "academic_year": YEAR,
                    "source_url": selected_rule.source_url,
                    "note": (
                        "Lower first-year undergraduate award: £2,000 "
                        "for the reviewed Writtle route, subject to "
                        "the saved eligibility conditions."
                        if undergraduate
                        else (
                            "International postgraduate scholarship: "
                            "up to £4,000 for eligible ARU graduates. "
                            "The maximum is an estimate; ARU confirms "
                            "the applicant's actual award."
                        )
                    ),
                    "eligibility_questions": merge_questions(
                        selected_rule.rules.get(
                            "eligibility_questions", []
                        ),
                        previous_review.get(
                            "eligibility_questions", []
                        ),
                    ),
                },
                "deposit_status": "verified_policy_eligibility_required",
                "deposit_review": {
                    **previous_deposit,
                    "status": "verified_policy_eligibility_required",
                    "policy_academic_year": YEAR,
                    "source_url": policy.source_url,
                    "note": (
                        "Saved £4,000 pre-CAS deposit policy scoped "
                        "to this reviewed route. Visa, full-fee-paying "
                        "and sponsorship conditions still apply. "
                        "Confirm the individual offer."
                    ),
                    "eligibility_questions": merge_questions(
                        deposit_after["eligibility_questions"],
                        previous_deposit.get(
                            "eligibility_questions", []
                        ),
                    ),
                },
            })

            if raw != source.raw_fees:
                course_changes.append({
                    "course_id": course_id,
                    "tuition_id": tuition.pk,
                    "source_id": source.pk,
                    "before": source.raw_fees,
                    "after": raw,
                })

        return {
            "rule_changes": rule_changes,
            "deposit_policy_id": policy.pk,
            "deposit_before": policy.rules,
            "deposit_after": deposit_after,
            "course_changes": course_changes,
            "ug_scope": len(
                set(ug_rule.rules["conditions"]["course_id"]) | {UG_ID}
            ),
            "pg_scope": len(
                set(pg_rule.rules["conditions"]["course_id"]) | PG_IDS
            ),
            "deposit_scope": len(deposit_after["course_ids"]),
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
            source = TuitionSource.objects.get(
                pk=change["source_id"]
            )
            source.raw_fees = change["after"]
            source.save(update_fields=["raw_fees"])

            TuitionUpdateAudit.objects.create(
                tuition_id=change["tuition_id"],
                course_identifier=change["course_id"],
                residency="INTL",
                before={"raw_fees": change["before"]},
                after={"raw_fees": change["after"]},
            )

    def handle(self, *args, **options):
        validator = RouteValidator()
        evidence_path = validator.find_evidence(options["evidence"])

        folder = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / (
                "land-engineering-awards-"
                + timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
            )
        )
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / "reconciliation.json"

        try:
            evidence = validator.read_report(evidence_path)

            if evidence.get("university_id") != "UK003":
                raise ValueError("Evidence is not for ARU UK003.")

            sources = self.fetch_policies()

            with transaction.atomic():
                if options["apply"]:
                    University.objects.select_for_update().get(
                        pk="UK003"
                    )

                plan = self.build_plan(
                    evidence,
                    evidence_path,
                    lock=options["apply"],
                )

                report = {
                    "generated_at": timezone.now().isoformat(),
                    "university_id": "UK003",
                    "academic_year": YEAR,
                    "mode": (
                        "apply_requested"
                        if options["apply"]
                        else "preview"
                    ),
                    "database_changes": False,
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
            requests.RequestException,
            OSError,
            ValueError,
            TypeError,
            ArithmeticError,
            KeyError,
            University.DoesNotExist,
        ) as exc:
            raise CommandError(
                f"Reconciliation stopped: {exc}. "
                "No database records changed."
            ) from exc

        count = (
            len(plan["rule_changes"])
            + len(plan["course_changes"])
            + int(
                plan["deposit_before"] != plan["deposit_after"]
            )
        )

        if options["apply"]:
            report["mode"] = "apply"
            report["database_changes"] = count > 0

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
            f"Lower UG scholarship scope: {plan['ug_scope']} courses"
        )
        self.stdout.write(
            f"ARU-graduate PG scholarship scope: "
            f"{plan['pg_scope']} courses"
        )
        self.stdout.write(
            f"£4,000 deposit scope: {plan['deposit_scope']} courses"
        )
        self.stdout.write(f"Proposed/updated items: {count}")
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                "Scholarship scopes, deposit scope and course "
                "review metadata saved."
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Eligibility questions preserved. Gross tuition, "
            "replacement history, stored scholarship amounts and "
            "numeric deposit fields unchanged. The comparison "
            "calculates applicable scholarship deductions."
        )
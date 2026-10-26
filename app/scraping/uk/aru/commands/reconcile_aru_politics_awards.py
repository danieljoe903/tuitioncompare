from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import requests

from django.conf import settings
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from app.models import (
    Course,
    DepositPolicy,
    ScholarshipRule,
    Tuition,
    TuitionSource,
)

from app.scraping.uk.aru.commands.reconcile_aru_games_health_awards import Command as PolicyTools, UG_URL, DEPOSIT_URL


COURSE_ID = "C_UK003_81"

COURSE_URL = (
    "https://www.aru.ac.uk/study/undergraduate/"
    "politics-and-international-relations"
)


def money(value):
    amount = Decimal(str(value))

    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")

    return format(amount.quantize(Decimal("0.01")), ".2f")


class Command(PolicyTools):
    """
    Reuse only the existing source collection, verification,
    reporting and update helpers.

    This class supplies its own Politics preparation and handler.
    """

    help = (
        "Reconcile 2027/28 scholarship and deposit applicability "
        "for Politics and International Relations."
    )

    def prepare_politics(self):
        updates = []

        course = Course.objects.select_for_update().get(
            university_id="UK003",
            course_id=COURSE_ID,
        )

        expected_course = {
            "title": "BA (Hons) Politics and International Relations",
            "level": "UG",
            "location": "Cambridge Campus",
            "duration": "3 Years",
            "start_month": "September",
            "start_year": 2027,
            "ucas_cao_code": "L203",
        }

        for field, expected in expected_course.items():
            if getattr(course, field) != expected:
                raise CommandError(
                    f"Unexpected saved course field: {field}. "
                    "Apply the reviewed replacement first."
                )

        if course.course_url.rstrip("/") != COURSE_URL:
            raise CommandError("Unexpected saved course URL.")

        rows = list(
            Tuition.objects.select_for_update().filter(
                course=course,
                residency="INTL",
            )
        )

        if len(rows) != 1 or rows[0].currency != "GBP":
            raise CommandError(
                "Expected one GBP international tuition."
            )

        tuition = rows[0]

        source = TuitionSource.objects.select_for_update().get(
            tuition=tuition
        )

        raw = deepcopy(source.raw_fees)

        if not isinstance(raw, dict):
            raise CommandError("Invalid tuition source metadata.")

        if (
            source.academic_year != "2027/28"
            or source.fee_basis != "annual_tuition"
            or source.source_url.rstrip("/") != COURSE_URL
            or raw.get("verified") is not True
            or raw.get("academic_year") != "2027/28"
            or raw.get("course_kind") != "undergraduate_taught"
            or money(raw.get("annual_tuition")) != "18825.00"
            or money(tuition.tuition_fee) != "18825.00"
        ):
            raise CommandError(
                "The saved verified tuition does not match "
                "the reviewed 2027/28 replacement."
            )

        rules = list(
            ScholarshipRule.objects.select_for_update().filter(
                scholarship__university_id="UK003",
                verified=True,
            )
        )

        summary = {}
        scholarship_questions = None

        for tier, award in [
            ("lower", "2000.00"),
            ("higher", "4000.00"),
        ]:
            matches = []

            for rule in rules:
                data = rule.rules

                if not isinstance(data, dict):
                    continue

                conditions = data.get("conditions", {})

                if not isinstance(conditions, dict):
                    continue

                if (
                    rule.source_url.rstrip("/")
                    == UG_URL.rstrip("/")
                    and data.get("academic_year") == "2027/28"
                    and data.get("kind") == "fixed"
                    and data.get("currency") == "GBP"
                    and money(data.get("value")) == award
                    and conditions.get("aru_ug_award_tier")
                    == [tier]
                ):
                    matches.append(rule)

            if len(matches) != 1:
                raise CommandError(
                    f"Expected one verified {tier} UG rule "
                    "for 2027/28."
                )

            rule = matches[0]
            data = deepcopy(rule.rules)
            conditions = data["conditions"]

            required_conditions = {
                "fee_status": ["INTL"],
                "new_entrant": [True],
                "full_time": [True],
                "entry_year": [1],
                "apcl": [False],
                "registration_conditions_met": [True],
                "aru_funding_eligibility_confirmed": [True],
                "aru_ug_award_confirmed": [True],
            }

            for key, expected in required_conditions.items():
                if conditions.get(key) != expected:
                    raise CommandError(
                        f"{tier} rule: unexpected condition {key}."
                    )

            if (
                data.get("stacking") != "exclusive"
                or data.get("study_years") != [1]
                or data.get("fee_basis_confirmed") is not True
            ):
                raise CommandError(
                    f"{tier} rule: award scope requires review."
                )

            questions = data.get("eligibility_questions")

            if not isinstance(questions, list) or not questions:
                raise CommandError(
                    f"{tier} rule has no saved eligibility questions."
                )

            scope = conditions.get("course_id")

            if not isinstance(scope, list) or not all(
                isinstance(value, str) for value in scope
            ):
                raise CommandError(
                    f"{tier} rule has an invalid course scope."
                )

            if COURSE_ID not in scope:
                conditions["course_id"] = sorted(
                    set(scope) | {COURSE_ID}
                )

            self.add_update(
                updates,
                "ScholarshipRule",
                rule,
                {"rules": data},
            )

            summary[f"{tier}_ug_scope"] = len(
                conditions["course_id"]
            )

            if tier == "lower":
                scholarship_questions = deepcopy(questions)

        policy = DepositPolicy.objects.select_for_update().get(
            university_id="UK003",
            academic_year="2027/28",
            currency="GBP",
            verified=True,
        )

        deposit_rules = deepcopy(policy.rules)

        if not isinstance(deposit_rules, dict):
            raise CommandError("Invalid deposit policy.")

        if (
            policy.source_url.rstrip("/")
            != DEPOSIT_URL.rstrip("/")
            or deposit_rules.get("kind") != "fixed"
            or deposit_rules.get("academic_year") != "2027/28"
            or deposit_rules.get("currency") != "GBP"
            or money(deposit_rules.get("amount")) != "4000.00"
            or deposit_rules.get("fee_status") != "INTL"
            or deposit_rules.get("payment_stage") != "before_cas"
            or deposit_rules.get("requires_student_visa") is not True
            or deposit_rules.get("full_fee_paying") is not True
            or deposit_rules.get(
                "deduct_scholarship_from_deposit"
            ) is not False
            or deposit_rules.get(
                "sponsorship_letter_requires_review"
            ) is not True
            or deposit_rules.get(
                "offer_letter_confirmation_required"
            ) is not True
            or "undergraduate_taught"
            not in deposit_rules.get("course_kinds", [])
        ):
            raise CommandError(
                "The saved fixed deposit policy requires review."
            )

        deposit_scope = deposit_rules.get("course_ids")
        deposit_questions = deposit_rules.get(
            "eligibility_questions"
        )

        if not isinstance(deposit_scope, list) or not all(
            isinstance(value, str) for value in deposit_scope
        ):
            raise CommandError("Invalid deposit course scope.")

        if (
            not isinstance(deposit_questions, list)
            or not deposit_questions
        ):
            raise CommandError(
                "Deposit eligibility questions are missing."
            )

        if COURSE_ID not in deposit_scope:
            deposit_rules["course_ids"] = sorted(
                set(deposit_scope) | {COURSE_ID}
            )

        self.add_update(
            updates,
            "DepositPolicy",
            policy,
            {"rules": deposit_rules},
        )

        raw["scholarship_status"] = (
            "verified_rule_eligibility_required"
        )

        raw["scholarship_review"] = {
            "status": "verified_rule_eligibility_required",
            "academic_year": "2027/28",
            "source_url": UG_URL,
            "note": (
                "Year 1 lower award £2,000 or higher award £4,000, "
                "subject to the saved eligibility conditions and "
                "ARU assessment. The awards are alternatives and "
                "must not be added together. The published policy "
                "is undated and is associated with this reviewed "
                "2027/28 route; recheck before enrolment."
            ),
            "eligibility_questions": scholarship_questions,
        }

        raw["deposit_status"] = (
            "verified_rule_eligibility_required"
        )

        raw["deposit_review"] = {
            "status": "verified_rule_eligibility_required",
            "policy_academic_year": "2027/28",
            "source_url": DEPOSIT_URL,
            "note": (
                "Current fixed pre-CAS deposit £4,000, subject "
                "to visa, full-fee-paying and sponsorship assessment. "
                "Confirm the individual offer. Scholarship deductions "
                "do not reduce this fixed deposit. The published "
                "deposit policy is undated."
            ),
            "eligibility_questions": deepcopy(deposit_questions),
        }

        self.add_update(
            updates,
            "TuitionSource",
            source,
            {"raw_fees": raw},
        )

        summary.update({
            "course_id": COURSE_ID,
            "academic_year": "2027/28",
            "gross_tuition": "18825.00",
            "lower_award_net": "16825.00",
            "higher_award_net": "14825.00",
            "conditional_deposit": "4000.00",
            "deposit_scope": len(deposit_rules["course_ids"]),
            "held_course_id": "C_UK003_82",
            "held_course_changed": False,
        })

        return updates, summary

    def handle(self, *args, **options):
        # Reuse the working source collector and policy checks
        # from the Games/Health reconciliation command.
        try:
            sources = self.collect_sources()
        except (
            requests.RequestException,
            ValueError,
            TypeError,
        ) as exc:
            raise CommandError(
                f"Source verification stopped: {exc}. "
                "No database records changed."
            ) from exc

        stamp = timezone.now().strftime("%Y%m%dT%H%M%S%fZ")
        output = (
            Path(settings.BASE_DIR)
            / "logs"
            / "aru"
            / f"politics-awards-{stamp}"
            / "reconciliation.json"
        )

        with transaction.atomic():
            try:
                updates, summary = self.prepare_politics()

            except (
                ValueError,
                TypeError,
                KeyError,
                ArithmeticError,
                Course.DoesNotExist,
                TuitionSource.DoesNotExist,
                DepositPolicy.DoesNotExist,
                DepositPolicy.MultipleObjectsReturned,
            ) as exc:
                raise CommandError(
                    f"Saved-rule verification stopped: {exc}. "
                    "No database records changed."
                ) from exc

            report = {
                "generated_at": timezone.now().isoformat(),
                "university_id": "UK003",
                "mode": "preview",
                "database_changes": False,
                "sources": sources,
                "summary": summary,
                "updates": updates,
            }

            try:
                self.write_report(output, report)
            except OSError as exc:
                raise CommandError(
                    f"Cannot save preview report: {exc}. "
                    "No database records changed."
                ) from exc

            if options["apply"]:
                self.apply_updates(updates)

        if options["apply"]:
            report["mode"] = "apply"
            report["database_changes"] = bool(updates)

            try:
                self.write_report(output, report)
            except OSError as exc:
                raise CommandError(
                    "Database changes were committed, but the final "
                    f"report could not be updated: {exc}"
                ) from exc

        self.stdout.write(
            f"Lower UG scholarship scope: "
            f"{summary['lower_ug_scope']} courses"
        )
        self.stdout.write(
            f"Higher UG scholarship scope: "
            f"{summary['higher_ug_scope']} courses"
        )
        self.stdout.write(
            f"2027/28 deposit scope: "
            f"{summary['deposit_scope']} courses"
        )
        self.stdout.write(
            "Politics: conditional £2,000/£4,000 award; "
            "net £16,825/£14,825 if eligible."
        )
        self.stdout.write(
            "Deposit: £4,000 subject to payment eligibility."
        )
        self.stdout.write(
            "C_UK003_82: unchanged; no duplicate mapping."
        )
        self.stdout.write(
            f"Proposed/updated items: {len(updates)}"
        )
        self.stdout.write(f"Report: {output}")

        if options["apply"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "Politics scholarship scopes, deposit scope "
                    "and review metadata saved."
                )
            )
        else:
            self.stdout.write(
                "Preview only. No database records changed."
            )

        self.stdout.write(
            "Eligibility questions and replacement history preserved. "
            "Gross tuition and numeric stored discounts/deposits "
            "unchanged. The comparison calculates eligible "
            "scholarship deductions from the saved rules."
        )
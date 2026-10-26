"""Read ARU deposit policies and eligibility questions from the database."""

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from app.models import DepositPolicy, ScholarshipRule


UNIVERSITY_ID = "UK003"


def boolean_answer(value):
    """Unknown answers stay unknown."""
    if value is True or value is False:
        return value

    if isinstance(value, str):
        value = value.strip().casefold()

        if value in {"yes", "true", "1"}:
            return True

        if value in {"no", "false", "0"}:
            return False

    return None


def saved_policy(academic_year):
    return DepositPolicy.objects.filter(
        university_id=UNIVERSITY_ID,
        academic_year=academic_year,
        currency="GBP",
        verified=True,
    ).first()


def get_aru_eligibility_questions(*, tuition, academic_year):
    """
    Return saved questions for this course and academic year.

    Does not infer answers or modify the database.
    """
    result = {
        "scholarship": [],
        "deposit": [],
    }

    if tuition.course.university_id != UNIVERSITY_ID:
        return result

    questions = {}
    allowed_tiers = set()

    rules = ScholarshipRule.objects.filter(
        scholarship__university_id=UNIVERSITY_ID,
        verified=True,
    )

    for saved_rule in rules:
        rule = saved_rule.rules

        if not isinstance(rule, dict):
            continue

        conditions = rule.get("conditions", {})

        if not isinstance(conditions, dict):
            continue

        if (
            rule.get("academic_year") != academic_year
            or str(tuition.course_id)
            not in conditions.get("course_id", [])
        ):
            continue

        allowed_tiers.update(
            conditions.get("aru_ug_award_tier", [])
        )

        for question in rule.get("eligibility_questions", []):
            if (
                isinstance(question, dict)
                and isinstance(question.get("key"), str)
            ):
                questions.setdefault(
                    question["key"], deepcopy(question)
                )

    tier_question = questions.get("aru_ug_award_tier")

    if tier_question is not None:
        tier_question["choices"] = [
            choice
            for choice in tier_question.get("choices", [])
            if choice.get("value") in allowed_tiers
        ]

    result["scholarship"] = list(questions.values())

    policy = saved_policy(academic_year)

    if policy and isinstance(policy.rules, dict):
        if str(tuition.course_id) in policy.rules.get(
            "course_ids", []
        ):
            result["deposit"] = deepcopy(
                policy.rules.get("eligibility_questions", [])
            )

    return result


def calculate_aru_deposit(
    *,
    tuition,
    academic_year,
    profile,
):
    """
    Calculate the fixed deposit from the saved ARU policy.

    The deposit is part of tuition, not an additional fee.
    Scholarship discounts are not subtracted from this fixed amount.
    """
    result = {
        "available": False,
        "amount": None,
        "currency": "GBP",
        "academic_year": academic_year,
        "source_url": None,
        "policy_checked_at": None,
        "rule": None,
        "is_estimate": True,
        "label": "Deposit assessment required",
        "note": "",
        "missing_facts": [],
    }

    def unavailable(note, missing=None):
        return {
            **result,
            "note": note,
            "missing_facts": missing or [],
        }

    if tuition.course.university_id != UNIVERSITY_ID:
        return unavailable("This calculation applies to ARU only.")

    if tuition.residency != "INTL" or tuition.currency != "GBP":
        return unavailable(
            "This policy applies to international tuition in GBP."
        )

    policy = saved_policy(academic_year)

    if policy is None:
        return unavailable(
            "No verified ARU deposit policy is saved "
            "for this academic year."
        )

    result["source_url"] = policy.source_url
    result["policy_checked_at"] = (
        policy.checked_at.isoformat()
        if policy.checked_at else None
    )

    rules = policy.rules

    if not isinstance(rules, dict):
        return unavailable("The saved deposit policy requires review.")

    if (
        rules.get("schema_version") != 1
        or rules.get("kind") != "fixed"
        or rules.get("currency") != "GBP"
        or rules.get("fee_status") != "INTL"
        or rules.get("payment_stage") != "before_cas"
        or rules.get("academic_year") != academic_year
        or rules.get("requires_student_visa") is not True
        or rules.get("full_fee_paying") is not True
        or rules.get("deduct_scholarship_from_deposit") is not False
    ):
        return unavailable("The saved deposit rule is unsupported.")

    scope = rules.get("course_ids")

    if not isinstance(scope, list) or str(tuition.course_id) not in scope:
        return unavailable(
            "This course has not been included in the "
            "reviewed deposit policy."
        )

    answers = {
        key: boolean_answer(profile.get(key))
        for key in (
            "requires_student_visa",
            "full_fee_paying",
            "sponsored",
        )
    }

    missing = [
        key for key, value in answers.items()
        if value is None
    ]

    if missing:
        return unavailable(
            "Confirm the visa, fee-paying and sponsorship "
            "answers to calculate the applicable deposit.",
            missing,
        )

    if not answers["requires_student_visa"]:
        return unavailable(
            "The pre-CAS rule does not apply without a Student "
            "visa requirement. Confirm payment arrangements with ARU."
        )

    if answers["sponsored"]:
        return unavailable(
            "A sponsorship letter may affect the payment requirement. "
            "ARU must confirm the deposit for this arrangement."
        )

    if not answers["full_fee_paying"]:
        return unavailable(
            "This published rule covers full-fee-paying international "
            "students. Confirm your payment arrangement with ARU."
        )

    try:
        amount = Decimal(str(rules["amount"]))

        if (
            not amount.is_finite()
            or amount <= 0
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise ValueError("Invalid deposit amount.")

    except (KeyError, InvalidOperation, ValueError, TypeError):
        return unavailable("The saved deposit amount requires review.")

    return {
        **result,
        "available": True,
        "amount": format(amount, ".2f"),
        "rule": "fixed_before_cas",
        "label": "Published deposit before CAS",
        "note": (
            f"The saved ARU policy specifies £{amount:,.2f} "
            "before CAS for this payment arrangement. "
            "Confirm the final requirement in your offer letter. "
            "This payment forms part of tuition; do not add it "
            "to the tuition total. Scholarship discounts have "
            "not been subtracted from the fixed deposit."
        ),
        "missing_facts": [],
    }
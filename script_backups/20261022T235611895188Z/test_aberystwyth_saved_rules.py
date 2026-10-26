"""Read-only checks of saved Aberystwyth scholarship/deposit rules."""

from decimal import Decimal
from types import SimpleNamespace

from app.models import (
    Course,
    DepositPolicy,
    Scholarship,
    Tuition,
    TuitionSource,
)
from app.scholarship_calculation import calculate_scholarships


YEAR = "2027/28"

ELIGIBLE_PROFILE = {
    "new_entrant": True,
    "academic_conditions_met": True,
    "offer_received": True,
    "applied_by_deadline": True,
    "external_funding": False,
}

GROUPS = [
    # Level, band, course count, tuition, permitted deduction
    ("UG", "Humanities", 22, "20400.00", "3000.00"),
    ("UG", "Sciences", 18, "23255.00", "0.00"),
    ("PG", "Humanities", 10, "22115.00", "3000.00"),
    ("PG", "Sciences", 5, "23820.00", "3800.00"),
]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def calculate(tuition, award, profile, year=YEAR):
    return calculate_scholarships(
        tuition,
        [award],
        profile,
        academic_year=year,
        study_year=1,
        allow_unverified_legacy=False,
    )


checked = 0

for level, band, expected_count, gross, deduction in GROUPS:
    name = (
        f"Global Citizens Scholarship — "
        f"{band} {level} ({YEAR})"
    )

    award = Scholarship.objects.select_related(
        "calculation_rule"
    ).get(
        university_id="UK002",
        name=name,
        level=level,
    )

    rule = award.calculation_rule
    course_ids = rule.rules["conditions"]["course_id"]

    require(
        len(course_ids) == expected_count,
        f"{name}: unexpected course count.",
    )
    require(
        len(set(course_ids)) == len(course_ids),
        f"{name}: duplicate course IDs.",
    )

    expected_verified = not (
        level == "UG" and band == "Sciences"
    )

    require(
        rule.verified is expected_verified,
        f"{name}: unexpected verification status.",
    )

    for course_id in course_ids:
        course = Course.objects.get(
            pk=course_id,
            university_id="UK002",
            is_active=True,
        )

        tuition = Tuition.objects.get(
            course=course,
            residency="INTL",
        )

        source = TuitionSource.objects.get(tuition=tuition)

        require(
            source.academic_year == YEAR,
            f"{course_id}: unexpected fee year.",
        )
        require(
            source.raw_fees.get("fee_band") == band,
            f"{course_id}: unexpected fee band.",
        )
        require(
            Decimal(str(tuition.tuition_fee)) == Decimal(gross),
            f"{course_id}: unexpected tuition.",
        )

        # Eligible answers: apply only the verified rule.
        result = calculate(tuition, award, ELIGIBLE_PROFILE)

        require(
            Decimal(result["discount"]) == Decimal(deduction),
            f"{course_id}: incorrect eligible deduction.",
        )
        require(
            Decimal(result["net"])
            == Decimal(gross) - Decimal(deduction),
            f"{course_id}: incorrect net tuition.",
        )

        if Decimal(deduction) > 0:
            require(
                result["label"] == "Estimated net tuition if awarded",
                f"{course_id}: conditional award labelled incorrectly.",
            )
        else:
            require(
                not result["applied"],
                f"{course_id}: unverified award was applied.",
            )

        # Missing eligibility answers must not trigger a deduction.
        result = calculate(tuition, award, {})

        require(
            Decimal(result["discount"]) == 0,
            f"{course_id}: missing answers allowed a deduction.",
        )

        # Test each required eligibility condition independently.
        for key in ELIGIBLE_PROFILE:
            profile = dict(ELIGIBLE_PROFILE)
            profile.pop(key)

            result = calculate(tuition, award, profile)

            require(
                Decimal(result["discount"]) == 0,
                f"{course_id}: missing {key} allowed a deduction.",
            )

        # External funding makes this award inapplicable.
        profile = dict(ELIGIBLE_PROFILE, external_funding=True)
        result = calculate(tuition, award, profile)

        require(
            Decimal(result["discount"]) == 0,
            f"{course_id}: external funding allowed a deduction.",
        )

        # The 2027/28 rule must not apply to another fee year.
        result = calculate(
            tuition, award, ELIGIBLE_PROFILE, year="2026/27"
        )

        require(
            Decimal(result["discount"]) == 0,
            f"{course_id}: award applied to the wrong year.",
        )

        # Simulate an old stored discount without saving it.
        comparison_tuition = SimpleNamespace(
            tuition_fee=tuition.tuition_fee,
            scholarship=Decimal("9999.00"),
            scholarship_source_id=None,
            currency=tuition.currency,
            residency=tuition.residency,
            course=course,
        )

        result = calculate(comparison_tuition, award, {})

        require(
            Decimal(result["discount"]) == 0,
            f"{course_id}: unverified legacy discount was deducted.",
        )

        checked += 1

    print(f"PASS: {level} {band} — {expected_count} courses")


# Check the actual saved deposit policy.
policy = DepositPolicy.objects.get(
    university_id="UK002",
    academic_year=YEAR,
)

require(policy.verified is True, "Deposit policy is not verified.")
require(policy.currency == "GBP", "Unexpected deposit currency.")
require(bool(policy.source_url), "Deposit source URL is missing.")

rules = policy.rules

require(
    rules.get("kind") == "offer_letter",
    "Unexpected deposit policy type.",
)
require(
    rules.get("amount") is None,
    "An unsupported universal deposit amount was saved.",
)
require(
    rules.get("percentage") is None,
    "An unsupported universal deposit percentage was saved.",
)
require(
    rules.get("amount_requires_individual_confirmation") is True,
    "Offer-letter confirmation requirement is missing.",
)

print("PASS: Deposit policy requires the individual offer-letter amount.")
print(f"\nPassed saved-rule checks for {checked} courses.")
print("No database records changed.")
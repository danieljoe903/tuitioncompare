"""Calculate Abertay deposit estimates from saved database policy."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.models import DepositPolicy


COUNTRY_ALIASES = {
    "NIGERIA": "NG",
    "AFGHANISTAN": "AF",
    "IRAN": "IR",
    "PAKISTAN": "PK",
    "BANGLADESH": "BD",
    "INDIA": "IN",
}


def money(value):
    amount = Decimal(str(value))

    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid monetary amount.")

    return amount.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def percentage(value):
    amount = Decimal(str(value))

    if (
        not amount.is_finite()
        or amount <= 0
        or amount > 100
    ):
        raise ValueError("Invalid percentage.")

    return amount

def calculate_aberystwyth_deposit(*, academic_year):
    policy = DepositPolicy.objects.filter(
        university_id="UK002",
        academic_year=academic_year,
        currency="GBP",
        verified=True,
    ).first()

    result = {
        "available": False,
        "amount": None,
        "currency": "GBP",
        "academic_year": academic_year,
        "source_url": None,
        "policy_checked_at": None,
        "rule": None,
        "is_estimate": False,
        "label": "Deposit assessment required",
        "note": (
            "No verified deposit policy is saved for this academic year."
        ),
    }

    if policy is None:
        return result

    rules = policy.rules

    if (
        not isinstance(rules, dict)
        or rules.get("kind") != "offer_letter"
    ):
        return {
            **result,
            "note": "The saved deposit policy requires review.",
        }

    return {
        **result,
        "source_url": policy.source_url,
        "policy_checked_at": policy.checked_at.isoformat(),
        "rule": "offer_letter",
        "label": "Confirm deposit in your offer letter",
        "note": rules["note"],
    }

def calculate_abertay_deposit(
    *,
    academic_year,
    tuition_fee,
    scholarship_calculation,
    legal_residence,
    residence_region="",
    requires_student_visa=None,
    self_funded=None,
):
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
    }

    def unavailable(reason):
        return {**result, "note": reason}

    policy = DepositPolicy.objects.filter(
        university_id="UK001",
        academic_year=academic_year,
        verified=True,
        currency="GBP",
    ).first()

    if policy is None:
        return unavailable(
            "No verified deposit policy is saved for this academic year."
        )

    result["source_url"] = policy.source_url
    result["policy_checked_at"] = policy.checked_at.isoformat()

    rules = policy.rules

    if not isinstance(rules, dict):
        return unavailable("Saved deposit policy requires review.")

    if (
        rules.get("schema_version") != 1
        or rules.get("fee_status") != "INTL"
        or rules.get("payment_stage") != "before_cas"
        or rules.get("basis") != "net_tuition"
        or rules.get("requires_student_visa") is not True
        or rules.get("self_funded") is not True
    ):
        return unavailable("Saved deposit policy is unsupported.")
    residence = (legal_residence or "").strip().upper()
    residence = COUNTRY_ALIASES.get(residence, residence)

    if not residence:
        return {
            **result,
            "label": "General deposit guidance",
            "note": (
                f"Normally {rules['standard_percentage']}% of net "
                "tuition; country-specific exceptions apply. "
                "Select your legal residence and confirm your "
                "visa and funding status for an estimate."
            ),
    }
    if requires_student_visa is None:
        return unavailable(
            "Confirm whether the student requires a Student visa."
        )

    if requires_student_visa is not True:
        return unavailable(
            "The pre-CAS deposit rule does not apply to students "
            "who do not require a Student visa. "
            "Confirm payment arrangements with Abertay."
        )

    if self_funded is not True:
        return unavailable(
            "Confirm that the student is self-funded. "
            "Sponsored students require separate payment assessment."
        )

    residence = (legal_residence or "").strip().upper()
    residence = COUNTRY_ALIASES.get(residence, residence)

    if not re_country_code(residence):
        return unavailable(
            "Select a specific legal country of residence."
        )

    region = " ".join(
        (residence_region or "").casefold().split()
    )

    region_rules = rules.get("full_payment_regions")
    fixed_rules = rules.get("country_fixed_amounts")
    full_countries = rules.get("full_payment_countries")

    if (
        not isinstance(region_rules, dict)
        or not isinstance(fixed_rules, dict)
        or not isinstance(full_countries, list)
        or not all(
            isinstance(values, list)
            for values in region_rules.values()
        )
    ):
        return unavailable("Saved country deposit rules require review.")

    if residence in region_rules and not region:
        return unavailable(
            "The state or region of legal residence is required "
            "because deposit rules differ by region."
        )

    if not isinstance(scholarship_calculation, dict):
        return unavailable("A tuition calculation is required.")

    try:
        gross = money(tuition_fee)
        discount = money(scholarship_calculation["discount"])
        net = money(scholarship_calculation["net"])

        if (
            gross <= 0
            or discount > gross
            or net != gross - discount
        ):
            raise ValueError("Inconsistent tuition calculation.")

        if residence in fixed_rules:
            deposit = money(fixed_rules[residence])
            selected_rule = "country_fixed_amount"

        elif (
            residence in full_countries
            or region in region_rules.get(residence, [])
        ):
            rate = percentage(rules["full_payment_percentage"])
            deposit = money(net * rate / Decimal("100"))
            selected_rule = "full_net_tuition"

        else:
            rate = percentage(rules["standard_percentage"])
            deposit = money(net * rate / Decimal("100"))
            selected_rule = "percentage_of_net_tuition"

        if deposit > net:
            return unavailable(
                "The published deposit exceeds the calculated net "
                "tuition. Abertay must confirm the amount payable."
            )

    except (
        InvalidOperation,
        ValueError,
        TypeError,
        KeyError,
    ):
        return unavailable(
            "The saved policy or tuition calculation requires review."
        )

    notes = [
        "Calculated from Abertay's saved deposit policy for a new, "
        "self-funded student requiring a Student visa.",
        "Abertay must confirm the final payment in the offer "
        "or CAS process.",
        "Early-payment discounts have not been included.",
    ]

    if rules.get("individual_override_possible") is True:
        notes.append(
            "Abertay may require a higher deposit after an "
            "individual visa-risk assessment."
        )

    if discount > 0:
        notes.append(
            "Any scholarship used must be awarded and confirmed by "
            "Abertay. Self-reported eligibility does not confirm an award."
        )

    return {
        **result,
        "available": True,
        "amount": str(deposit),
        "rule": selected_rule,
        "label": "Estimated deposit before CAS",
        "note": " ".join(notes),
    }


def re_country_code(value):
    return (
        len(value) == 2
        and value.isascii()
        and value.isalpha()
    )
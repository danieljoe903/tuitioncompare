"""Conservative tuition-award matching. No database writes or text inference."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def evaluate_award(
    award,
    rule,
    context,
    fee,
    currency,
    academic_year,
    study_year,
    today=None,
):
    today = today or date.today()

    if (
        not isinstance(rule, dict)
        or rule.get("verified") is not True
        or not rule.get("source_url")
    ):
        return None, "Rules have not been verified against a source"

    rules = rule.get("rules", {})

    required = {
        "benefit",
        "kind",
        "value",
        "academic_year",
        "study_years",
        "conditions",
        "award_status",
        "stacking",
        "compatible_ids",
        "percentage_basis",
        "fee_basis_confirmed",
    }

    if not isinstance(rules, dict) or not required.issubset(rules):
        return None, "Incomplete structured rules"

    if rules["fee_basis_confirmed"] is not True:
        return None, "Applicable tuition base has not been confirmed"

    if rules["benefit"] != "tuition":
        return None, "Award is not a tuition discount"

    if (
        not isinstance(rules["study_years"], list)
        or rules["academic_year"] != academic_year
        or study_year not in rules["study_years"]
    ):
        return None, "Award period does not match the fee period"

    if award.get("deadline") and today > award["deadline"]:
        return None, "Application deadline has passed"

    conditions = rules["conditions"]

    if not isinstance(conditions, dict):
        return None, "Conditions must be an object"

    # Unknown answers are never treated as confirmed eligibility.
    for key, allowed in conditions.items():
        if key not in context or context[key] is None:
            return None, f"Eligibility fact not confirmed: {key}"

        if not isinstance(allowed, list) or not allowed:
            return None, f"Invalid eligibility rule: {key}"

        if context[key] not in allowed:
            return None, f"Eligibility condition not met: {key}"

    if rules["award_status"] not in (
        "automatic",
        "competitive",
        "conditional",
    ):
        return None, "Unknown award status"

    if (
        rules["stacking"] not in ("exclusive", "explicit")
        or not isinstance(rules["compatible_ids"], list)
    ):
        return None, "Combination rules have not been confirmed"

    try:
        fee = Decimal(str(fee))
        value = Decimal(str(rules["value"]))

        if (
            not fee.is_finite()
            or fee < 0
            or not value.is_finite()
            or value <= 0
        ):
            raise ValueError()

        if rules["kind"] in ("fixed", "fixed_maximum"):
            if rules.get("currency") != currency:
                return None, "Award currency differs from fee currency"

            amount = value

        elif rules["kind"] == "percentage":
            if (
                value > 100
                or rules["percentage_basis"] != "original_tuition"
            ):
                return None, (
                    "Percentage basis is unsupported or unconfirmed"
                )

            amount = fee * value / Decimal("100")

        else:
            return None, "Unsupported award type"

    except (InvalidOperation, ValueError, TypeError):
        return None, "Invalid award amount"

    return {
        "id": str(award["id"]),
        "name": award["name"],
        "amount": min(fee, amount),
        "status": rules["award_status"],
        "stacking": rules["stacking"],
        "compatible_ids": [
            str(value) for value in rules["compatible_ids"]
        ],
        "is_maximum": rules["kind"] == "fixed_maximum",
    }, None


def select_awards(candidates, fee):
    """Select the best permitted combination of tuition awards."""

    if len(candidates) > 15:
        # Avoid exponential work for unusually large candidate lists.
        return [
            max(candidates, key=lambda candidate: candidate["amount"])
        ], True

    best = []
    best_total = Decimal("0")

    for mask in range(1, 1 << len(candidates)):
        group = [
            candidate
            for index, candidate in enumerate(candidates)
            if mask & (1 << index)
        ]

        # Every pair must explicitly permit combining with each other.
        if len(group) > 1 and any(
            first["stacking"] != "explicit"
            or second["stacking"] != "explicit"
            or second["id"] not in first["compatible_ids"]
            or first["id"] not in second["compatible_ids"]
            for index, first in enumerate(group)
            for second in group[index + 1:]
        ):
            continue

        total = min(
            fee,
            sum(
                (candidate["amount"] for candidate in group),
                Decimal("0"),
            ),
        )

        if total > best_total:
            best = group
            best_total = total

    return best, False


def calculate_scholarships(
    tuition,
    scholarships,
    profile,
    academic_year,
    study_year,
    allow_unverified_legacy=True,
):
    fee = Decimal(str(tuition.tuition_fee))
    legacy = Decimal(str(tuition.scholarship or 0))

    if (
        not fee.is_finite()
        or fee < 0
        or not legacy.is_finite()
        or legacy < 0
    ):
        raise ValueError(
            "Tuition and stored discount must be finite "
            "non-negative amounts"
        )

    linked_id = getattr(tuition, "scholarship_source_id", None)

    context = dict(
        profile,
        course_id=str(tuition.course.course_id),
        level=tuition.course.level,
        fee_status=tuition.residency,
    )

    review = []
    notes = []
    candidates = []
    scholarships = list(scholarships)

    # Preserve unidentified stored deductions, without stacking new awards.
    if legacy > 0 and not linked_id and allow_unverified_legacy:
        discount = min(fee, legacy)

        return {
            "discount": str(discount),
            "net": str(max(Decimal("0"), fee - discount)),
            "label": (
                "Net tuition with stored discount "
                "(eligibility unverified)"
            ),
            "applied": [],
            "review": [],
            "notes": [
                "A stored discount was deducted. Its eligibility "
                "has not been verified; additional awards "
                "were not evaluated."
            ],
            "maximum_award_estimate": False,
            "academic_year": academic_year,
            "study_year": study_year,
        }

    # Linking identifies the legacy award; it must not exclude other awards.
    if linked_id and not any(
        scholarship.pk == linked_id
        for scholarship in scholarships
    ):
        notes.append(
            "The linked scholarship was not found "
            "in the candidate list."
        )

    if legacy > 0 and not linked_id and not allow_unverified_legacy:
        notes.append(
            "An unverified stored discount was excluded; "
            "only verified award rules were evaluated."
        )

    for scholarship in scholarships:
        try:
            rule = scholarship.calculation_rule

            rule_data = {
                "verified": rule.verified,
                "source_url": rule.source_url,
                "rules": rule.rules,
            }

        except AttributeError:
            rule_data = None

        candidate, reason = evaluate_award(
            {
                "id": scholarship.pk,
                "name": scholarship.name,
                "deadline": scholarship.deadline,
            },
            rule_data,
            context,
            fee,
            tuition.currency,
            academic_year,
            study_year,
        )

        if candidate:
            candidates.append(candidate)
        else:
            review.append({
                "id": str(scholarship.pk),
                "reason": reason,
            })

    selected, limited = select_awards(candidates, fee)

    if limited:
        notes.append(
            "More than 15 eligible awards: "
            "selected the best single award."
        )

    discount = min(
        fee,
        sum(
            (candidate["amount"] for candidate in selected),
            Decimal("0"),
        ),
    ).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    maximum_estimate = any(
        candidate.get("is_maximum", False)
        for candidate in selected
    )

    estimated = any(
        candidate["status"] != "automatic"
        for candidate in selected
    )

    if maximum_estimate:
        label = (
            "Estimated net tuition if maximum scholarship awarded"
        )

        notes.append(
            "This uses the published maximum annual award, "
            "not a guaranteed deduction. The university must "
            "confirm the award and amount."
        )

    elif estimated:
        label = "Estimated net tuition if awarded"

    elif selected:
        label = "Net tuition after automatic awards"

    else:
        label = "Tuition before unconfirmed awards"

    return {
        "discount": str(discount),
        "net": str(max(Decimal("0"), fee - discount)),
        "label": label,
        "maximum_award_estimate": maximum_estimate,
        "applied": [
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "amount": str(candidate["amount"]),
                "status": candidate["status"],
                "is_maximum": candidate.get("is_maximum", False),
            }
            for candidate in selected
        ],
        "review": review,
        "notes": notes,
        "academic_year": academic_year,
        "study_year": study_year,
    }
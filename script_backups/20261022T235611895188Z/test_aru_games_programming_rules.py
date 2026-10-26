import json
from decimal import Decimal

from django.test import RequestFactory
from django.urls import resolve


COURSE_ID = "C_UK003_65"
ENDPOINT = "/api/compare-tuition/"

factory = RequestFactory()

BASE_PARAMS = {
    "residence": "NG",
    "destination": "UK",
    "programme": "UG",
    "subject": "Computer Games Programming",
    "fee_status": "international",
    "new_entrant": "yes",
    "full_time": "yes",
    "entry_year": "1",
    "apcl": "no",
    "registration_conditions_met": "yes",
    "aru_funding_eligibility_confirmed": "yes",
    "aru_ug_award_confirmed": "yes",
    "aru_ug_award_tier": "lower",
    "requires_student_visa": "yes",
    "full_fee_paying": "yes",
    "sponsored": "no",
}


def amount(value):
    if value is None:
        raise AssertionError("Expected a monetary amount, received null.")

    return Decimal(str(value)).quantize(Decimal("0.01"))


def compare(params):
    request = factory.get(ENDPOINT, data=params)
    match = resolve(ENDPOINT)

    response = match.func(
        request,
        *match.args,
        **match.kwargs,
    )

    if hasattr(response, "render"):
        response.render()

    assert response.status_code == 200, (
        f"API returned HTTP {response.status_code}: "
        f"{response.content.decode('utf-8')[:1500]}"
    )

    payload = json.loads(response.content.decode("utf-8"))

    assert isinstance(payload, dict), "Expected a JSON object."
    assert isinstance(payload.get("results"), list), (
        f"Expected a results list. Received keys: {list(payload)}"
    )

    matches = [
        row
        for row in payload["results"]
        if row.get("course_id") == COURSE_ID
    ]

    assert len(matches) == 1, (
        f"Expected one Games Programming result; found {len(matches)}."
    )

    row = matches[0]

    assert row.get("academic_year") == "2027/28"
    assert row.get("fee_available") is True
    assert row.get("fee_verified") is True
    assert row.get("currency") == "GBP"
    assert amount(row.get("tuition_fee")) == Decimal("18825.00")

    return row


def check_scholarship(row, discount, net):
    calculation = row.get("scholarship_calculation")

    assert isinstance(calculation, dict), (
        "Missing scholarship_calculation."
    )

    expected_discount = Decimal(discount)
    expected_net = Decimal(net)

    assert amount(row.get("scholarship")) == expected_discount, (
        f"Unexpected scholarship: {row.get('scholarship')}. "
        f"Details: {calculation}"
    )

    assert amount(row.get("net_tuition")) == expected_net, (
        f"Unexpected net tuition: {row.get('net_tuition')}"
    )

    assert amount(calculation.get("discount")) == expected_discount
    assert amount(calculation.get("net")) == expected_net

    if expected_discount > 0:
        assert calculation.get("applied"), (
            "Discount was returned without an applied award."
        )

        assert calculation.get("maximum_award_estimate") is False, (
            "The confirmed UG award should not be a maximum estimate."
        )
    else:
        assert not calculation.get("applied"), (
            "An award was applied despite blocked eligibility."
        )


def check_deposit(row, available=True):
    calculation = row.get("deposit_calculation")

    assert isinstance(calculation, dict), (
        "Missing deposit_calculation."
    )

    assert calculation.get("academic_year") == "2027/28"

    if available:
        assert calculation.get("available") is True, calculation
        assert calculation.get("currency") == "GBP"
        assert amount(calculation.get("amount")) == Decimal("4000.00")
        assert amount(row.get("tuition_deposit")) == Decimal("4000.00")
    else:
        assert calculation.get("available") is False, calculation
        assert calculation.get("amount") is None
        assert row.get("tuition_deposit") is None


print("Testing synthetic eligibility scenarios.")
print("These answers do not confirm any real student's award.\n")

passed = 0

# 1. Lower award.
row = compare(dict(BASE_PARAMS))
check_scholarship(row, "2000.00", "16825.00")
check_deposit(row)
passed += 1
print("PASS: Lower award — £18,825 - £2,000 = £16,825; deposit £4,000.")

# 2. Higher award; alternatives must not stack.
params = dict(BASE_PARAMS)
params["aru_ug_award_tier"] = "higher"

row = compare(params)
check_scholarship(row, "4000.00", "14825.00")
check_deposit(row)
passed += 1
print("PASS: Higher award — £18,825 - £4,000 = £14,825; deposit £4,000.")

# 3. Missing award confirmation prevents deduction.
params = dict(BASE_PARAMS)
params.pop("aru_ug_award_confirmed")

row = compare(params)
check_scholarship(row, "0.00", "18825.00")
check_deposit(row)
passed += 1
print("PASS: Missing award confirmation blocks deduction.")

# 4. Explicit negative confirmation prevents deduction.
params = dict(BASE_PARAMS)
params["aru_ug_award_confirmed"] = "no"

row = compare(params)
check_scholarship(row, "0.00", "18825.00")
check_deposit(row)
passed += 1
print("PASS: Negative award confirmation blocks deduction.")

# 5. Unknown award tier must not default to an award.
params = dict(BASE_PARAMS)
params.pop("aru_ug_award_tier")

row = compare(params)
check_scholarship(row, "0.00", "18825.00")
check_deposit(row)
passed += 1
print("PASS: Missing award tier blocks deduction.")

# 6. APCL requires a separate assessment.
params = dict(BASE_PARAMS)
params["apcl"] = "yes"

row = compare(params)
check_scholarship(row, "0.00", "18825.00")
check_deposit(row)
passed += 1
print("PASS: APCL blocks the standard scholarship calculation.")

# 7. Missing visa answer prevents a deposit estimate.
params = dict(BASE_PARAMS)
params.pop("requires_student_visa")

row = compare(params)
check_scholarship(row, "2000.00", "16825.00")
check_deposit(row, available=False)
passed += 1
print("PASS: Missing visa answer blocks deposit; scholarship still applies.")

# 8. Sponsorship requires separate deposit assessment.
params = dict(BASE_PARAMS)
params["sponsored"] = "yes"

# Do not assume the student's funding remains scholarship-eligible.
params.pop("aru_funding_eligibility_confirmed")

row = compare(params)
check_scholarship(row, "0.00", "18825.00")
check_deposit(row, available=False)
passed += 1
print("PASS: Sponsorship requires deposit review; unknown funding blocks award.")

print(f"\nPassed API scenarios: {passed}")
print("No database records changed by this test script.")
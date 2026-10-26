"""Read-only API checks for five corrected ARU deposit cases."""

import json
import re
from decimal import Decimal

from django.test import RequestFactory
from django.urls import resolve

from app.models import Course


IDS = [
    "C_UK003_01",
    "C_UK003_05",
    "C_UK003_31",
    "C_UK003_36",
    "C_UK003_95",
]

ENDPOINT = "/api/compare-tuition/"
factory = RequestFactory()
match = resolve(ENDPOINT)

BASE = {
    "residence": "NG",
    "destination": "UK",
    "programme": "UG",
    "fee_status": "international",
    "new_entrant": "yes",
    "full_time": "yes",
    "entry_year": "1",
    "apcl": "no",
    "registration_conditions_met": "yes",
    "aru_funding_eligibility_confirmed": "yes",
    "requires_student_visa": "yes",
    "full_fee_paying": "yes",
    "sponsored": "no",
}


def money(value):
    assert value is not None, "Expected an amount, received null."
    return Decimal(str(value)).quantize(Decimal("0.01"))


def compare(course, params):
    request = factory.get(ENDPOINT, data=params)
    response = match.func(
        request, *match.args, **match.kwargs
    )

    if hasattr(response, "render"):
        response.render()

    assert response.status_code == 200, (
        f"{course.course_id}: HTTP {response.status_code}; "
        f"{response.content.decode('utf-8')[:1000]}"
    )

    payload = json.loads(response.content.decode("utf-8"))
    rows = payload.get("results")
    assert isinstance(rows, list), payload

    selected = [
        row for row in rows
        if row.get("course_id") == course.course_id
    ]
    assert len(selected) == 1, (
        f"{course.course_id}: expected one result, "
        f"found {len(selected)}. Search: {params['subject']!r}. "
        "This may be a search/filter issue, not a deposit failure."
    )

    row = selected[0]
    assert row.get("academic_year") == "2027/28", row
    assert row.get("fee_available") is True, row
    assert row.get("fee_verified") is True, row
    assert row.get("currency") == "GBP", row
    return row


def check_deposit(row, available):
    calculation = row.get("deposit_calculation")
    assert isinstance(calculation, dict), row
    assert calculation.get("available") is available, calculation

    if available:
        assert calculation.get("academic_year") == "2027/28"
        assert calculation.get("currency") == "GBP"
        assert money(calculation.get("amount")) == Decimal("4000.00")
        assert money(row.get("tuition_deposit")) == Decimal("4000.00")
    else:
        assert calculation.get("amount") is None, calculation
        assert row.get("tuition_deposit") is None, row


def check_scholarship(row, expected):
    calculation = row.get("scholarship_calculation")
    assert isinstance(calculation, dict), row

    gross = money(row.get("tuition_fee"))
    discount = Decimal(expected)
    net = gross - discount

    assert money(row.get("scholarship")) == discount, calculation
    assert money(calculation.get("discount")) == discount, calculation
    assert money(row.get("net_tuition")) == net, calculation
    assert money(calculation.get("net")) == net, calculation

    if discount:
        assert calculation.get("applied"), calculation
    else:
        assert not calculation.get("applied"), calculation


def run():
    courses = {
        course.course_id: course
        for course in Course.objects.filter(
            university_id="UK003", course_id__in=IDS
        )
    }
    assert set(courses) == set(IDS), "Some expected courses are missing."

    print("Testing synthetic answers; no real award is confirmed.")
    passed = 0

    for course_id in IDS:
        course = courses[course_id]

        # Use the current course title as the comparison search.
        subject = re.sub(
            r"^(?:BA|BSc|BEng|LLB)\s*(?:\(Hons\))?\s*",
            "",
            course.title,
            flags=re.IGNORECASE,
        ).strip()

        params = {**BASE, "subject": subject}

        row = compare(course, params)
        check_deposit(row, True)
        check_scholarship(row, "0.00")
        passed += 1
        print(f"PASS {course_id}: deposit GBP 4000; no unconfirmed award.")

        no_visa_answer = dict(params)
        no_visa_answer.pop("requires_student_visa")
        row = compare(course, no_visa_answer)
        check_deposit(row, False)
        passed += 1
        print(f"PASS {course_id}: missing visa answer blocks deposit.")

        sponsored = {**params, "sponsored": "yes"}
        row = compare(course, sponsored)
        check_deposit(row, False)
        passed += 1
        print(f"PASS {course_id}: sponsorship requires deposit review.")

        confirmed_award = {
            **params,
            "aru_ug_award_confirmed": "yes",
            "aru_ug_award_tier": "lower",
        }
        row = compare(course, confirmed_award)
        check_deposit(row, True)
        check_scholarship(row, "2000.00")
        passed += 1
        print(
            f"PASS {course_id}: "
            f"GBP {money(row['tuition_fee'])} - 2000.00 "
            f"= {money(row['net_tuition'])}; deposit GBP 4000."
        )
        print()

    print(f"Passed API scenarios: {passed}")
    print("No database records changed.")


run()
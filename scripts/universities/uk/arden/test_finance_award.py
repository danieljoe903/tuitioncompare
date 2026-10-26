"""Read-only actual comparison API checks; synthetic eligibility, no real award."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.arden.route_calculation import ONLINE_FACTS

factory = RequestFactory()
endpoint = '/api/compare-tuition/'
match = resolve(endpoint)
course = Course.objects.get(university_id='UK004', course_id='C_UK004_67')
base = dict(residence='NG', destination='UK', programme='PG', subject=course.title,
            comparison_basis='programme_total', arden_intake='2027-01')
eligible = {**base, **{k: 'yes' for k in ONLINE_FACTS},
            'arden_partner_route': 'no', 'arden_application_residence': 'NG'}


def compare(params):
    response = match.func(factory.get(endpoint, data=params), *match.args, **match.kwargs)
    if hasattr(response, 'render'):
        response.render()
    assert response.status_code == 200, response.content[:500]
    rows = [r for r in json.loads(response.content)['results'] if r['course_id'] == course.course_id]
    assert len(rows) == 1, rows
    return rows[0]


def money(v):
    return Decimal(str(v)).quantize(Decimal('.01'))


print('Synthetic eligibility only. No real award confirmed; no database writes.')
passed = 0
for country in ['NG', 'GH']:
    row = compare({**eligible, 'residence': country, 'arden_application_residence': country})
    assert money(row['tuition_fee']) == Decimal('10855.00'), row
    assert money(row['scholarship']) == Decimal('5970.25'), row
    assert money(row['net_tuition']) == Decimal('4884.75'), row
    assert row['fee_basis'] == 'total_programme_fee', row
    assert row['tuition_deposit'] is None, row
    passed += 1
for key in ONLINE_FACTS:
    absent = dict(eligible)
    absent.pop(key)
    assert money(compare(absent)['scholarship']) == 0, key
    negative = {**eligible, key: 'yes' if key == 'arden_partner_route' else 'no'}
    assert money(compare(negative)['scholarship']) == 0, key
    passed += 2
assert money(compare(base)['scholarship']) == 0
passed += 1
assert money(compare({**eligible, 'arden_application_residence': '', 'arden_application_nationality': ''})['scholarship']) == 0
passed += 1
for params in [dict(eligible, comparison_basis='annual'), dict(eligible, arden_intake='2027-04'), dict(eligible, arden_campus='Manchester')]:
    row = compare(params)
    assert row['fee_available'] is False, row
    assert row.get('scholarship') in (None, 0, 0.0, '0.00'), row
    passed += 1
print('Passed Finance scholarship API scenarios:', passed)
print('No database records changed.')

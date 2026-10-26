"""Actual endpoint tests after apply; synthetic facts only, no database writes."""
import json
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.arden.route_calculation import ONLINE_FACTS, ALUMNI_FACTS

scope = json.loads((Path(settings.BASE_DIR) / 'data/universities/uk/arden/online_alumni_scope.json').read_text(encoding='utf-8'))
factory = RequestFactory()
endpoint = '/api/compare-tuition/'
match = resolve(endpoint)
passed = 0


def compare(cid, params):
    response = match.func(factory.get(endpoint, data=params), *match.args, **match.kwargs)
    if hasattr(response, 'render'):
        response.render()
    assert response.status_code == 200, response.content[:500]
    rows = [r for r in json.loads(response.content)['results'] if r['course_id'] == cid]
    assert len(rows) == 1, (cid, rows)
    return rows[0]


def money(value):
    return Decimal(str(value)).quantize(Decimal('.01'))


print('Synthetic Alumni eligibility only. No real award confirmed.')
for item in scope:
    cid = item['course_id']
    course = Course.objects.get(university_id='UK004', course_id=cid)
    gross = Decimal(item['gross'])
    # No application-country answers: Global Tiers is deliberately not confirmed.
    base = dict(residence='NG', destination='UK', programme='PG', subject=course.title,
                comparison_basis='programme_total', arden_intake=item['intakes'][0])
    eligible = {**base, **{k: 'yes' for k in ONLINE_FACTS}, **{k: 'yes' for k in ALUMNI_FACTS}, 'arden_partner_route': 'no'}
    row = compare(cid, eligible)
    assert money(row['scholarship']) == gross / 2 and money(row['net_tuition']) == gross / 2, row
    assert money(row['tuition_fee']) == gross and row['fee_basis'] == 'total_programme_fee', row
    assert row['tuition_deposit'] is None, row
    passed += 1
    for key in ALUMNI_FACTS:
        absent = dict(eligible)
        absent.pop(key)
        assert money(compare(cid, absent)['scholarship']) == 0, (cid, key)
        assert money(compare(cid, {**eligible, key: 'no'})['scholarship']) == 0, (cid, key)
        passed += 2
    assert money(compare(cid, {**eligible, 'arden_partner_route': 'yes'})['scholarship']) == 0
    passed += 1
    annual = compare(cid, {**eligible, 'comparison_basis': 'annual'})
    assert annual['fee_available'] is False, annual
    passed += 1
    for country in ['NG', 'GH']:
        row = compare(cid, {**eligible, 'residence': country, 'arden_application_residence': country})
        expected = gross / 2
        if cid in {'C_UK004_36', 'C_UK004_66', 'C_UK004_67', 'C_UK004_69'}:
            expected = Decimal('3825.00') if cid.endswith('_69') else Decimal('4884.75') if cid in {'C_UK004_66','C_UK004_67'} else Decimal('4747.50')
        assert money(row['net_tuition']) == expected, row
        assert money(row['scholarship']) == gross - expected, row
        # Exclusive offers must never sum together.
        assert len(row['scholarship_calculation']['applied']) == 1, row
        passed += 1
    print(cid + ': Alumni-only net GBP ' + str(gross / 2) + '; eligibility and exclusive selection passed.')
print('Passed online Alumni API scenarios:', passed)
print('No database records changed.')

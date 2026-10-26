"""Synthetic API checks for the 11 imported Arden routes. No DB writes."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course, TuitionSource
from app.scraping.uk.arden.calculation import FACTS, DEADLINES

IDS = [f'C_UK004_{n}' for n in range(16, 26)] + ['C_UK004_40']
factory = RequestFactory()
endpoint = '/api/compare-tuition/'
match = resolve(endpoint)


def money(value):
    assert value is not None, 'Expected amount; received null.'
    return Decimal(str(value)).quantize(Decimal('.01'))


def compare(course, params):
    response = match.func(factory.get(endpoint, data=params), *match.args, **match.kwargs)
    if hasattr(response, 'render'):
        response.render()
    assert response.status_code == 200, response.content[:1000]
    rows = [r for r in json.loads(response.content)['results'] if r['course_id'] == course.course_id]
    assert len(rows) == 1, (course.course_id, len(rows))
    return rows[0]


def check(row, discount):
    assert row['fee_available'] is True and row['fee_verified'] is True, row
    assert row['academic_year'] == '2026/27', row
    assert money(row['tuition_fee']) == Decimal('17500'), row
    assert money(row['scholarship']) == Decimal(discount), row['scholarship_calculation']
    assert money(row['net_tuition']) == Decimal('17500') - Decimal(discount), row
    assert row['deposit_calculation']['rule'] == 'application_advised', row['deposit_calculation']
    assert row['deposit_calculation']['available'] is False
    assert row['tuition_deposit'] is None
    assert row['eligibility_questions']['scholarship']


def run():
    count = 0
    print('Synthetic eligibility only. These answers do not confirm a real award.')
    for cid in IDS:
        course = Course.objects.get(university_id='UK004', course_id=cid)
        source = TuitionSource.objects.get(tuition__course_id=cid, tuition__residency='INTL')
        raw = source.raw_fees
        base = dict(residence='NG', destination='UK', programme='PG', subject=course.title)
        base.update({key: 'yes' for key in FACTS})
        base['arden_partner_route'] = 'no'
        for campus, standard in [('Manchester', '4500'), ('London Stratford', '3500')]:
            intake = raw['campus_intakes'][campus][0]
            params = {**base, 'arden_campus': campus, 'arden_intake': intake}
            row = compare(course, params)
            check(row, standard)
            count += 1
            print(f'PASS {cid} {campus}: standard net GBP {row["net_tuition"]}')
            cutoff = datetime.fromisoformat(DEADLINES[intake] + 'T17:30:00').replace(tzinfo=ZoneInfo('Europe/London'))
            early = {**params, 'arden_unconditional_offer_at': cutoff.isoformat(), 'arden_deposit_paid_at': cutoff.isoformat()}
            check(compare(course, early), str(Decimal(standard) + 3000))
            count += 1
            late = {**early, 'arden_deposit_paid_at': (cutoff + timedelta(seconds=1)).isoformat()}
            check(compare(course, late), standard)
            count += 1
            naive = {**early, 'arden_deposit_paid_at': cutoff.replace(tzinfo=None).isoformat()}
            check(compare(course, naive), standard)
            count += 1
        missing_campus = dict(base)
        check(compare(course, missing_campus), '0')
        count += 1
        missing_visa = {**base, 'arden_campus': 'Manchester'}
        missing_visa.pop('requires_student_visa')
        check(compare(course, missing_visa), '0')
        count += 1
        excluded = {**base, 'arden_campus': 'Manchester', 'arden_partner_route': 'yes'}
        check(compare(course, excluded), '0')
        count += 1
        invalid = compare(course, {**base, 'arden_campus': 'Birmingham'})
        assert invalid['fee_available'] is False, invalid
        count += 1
        unsupported_intake = compare(course, {**base, 'arden_campus': 'Manchester', 'arden_intake': '2027-05'})
        assert unsupported_intake['fee_available'] is False, unsupported_intake
        count += 1
        print(f'PASS {cid}: missing/excluded facts and unsupported selections handled.\n')
    print('Passed API scenarios:', count)
    print('No database records changed.')


run()

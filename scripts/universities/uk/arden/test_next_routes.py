"""Synthetic API checks for the new routes and fee-basis separation. No DB writes."""
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course, TuitionSource
from app.scraping.uk.arden.route_calculation import ONLINE_FACTS
from app.scraping.uk.arden.calculation import FACTS, DEADLINES
from scripts.universities.uk.arden.import_next_routes import ONLINE_IDS, TIER2, TOPUP

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
    result = json.loads(response.content)
    rows = [r for r in result['results'] if r['course_id'] == course.course_id]
    assert len(rows) == 1, (course.course_id, len(rows))
    return rows[0], result


def run():
    passed = 0
    print('Synthetic eligibility only; no real scholarship award confirmed.')
    for cid in ONLINE_IDS:
        course = Course.objects.get(university_id='UK004', course_id=cid)
        source = TuitionSource.objects.get(tuition__course_id=cid, tuition__residency='INTL')
        gross = Decimal(source.raw_fees['total_programme_fee'])
        params = dict(residence='NG', destination='UK', programme='PG', subject=course.title)
        row, _ = compare(course, params)
        assert row['fee_available'] is False, row
        assert 'total programme' in row['fee_note'].lower(), row
        passed += 1
        params.update(comparison_basis='programme_total')
        row, payload = compare(course, params)
        assert row['fee_available'] is True and row['fee_verified'] is True, row
        assert row['fee_basis'] == 'total_programme_fee', row
        assert row['fee_label'] == 'Total programme fee', row
        assert money(row['tuition_fee']) == gross
        assert money(row['scholarship']) == 0
        assert money(row['net_tuition']) == gross
        assert row['deposit_calculation']['rule'] == 'campus_policy_not_applicable'
        assert row['tuition_deposit'] is None
        assert all(r.get('fee_basis') == 'total_programme_fee' for r in payload['results'] if r['fee_available']), payload
        passed += 1
        eligible = {**params, **{key: 'yes' for key in ONLINE_FACTS},
                    'arden_partner_route': 'no', 'arden_application_residence': 'NG'}
        row, _ = compare(course, eligible)
        expected_net = Decimal(TIER2[cid][1]) if cid in TIER2 else gross
        assert money(row['net_tuition']) == expected_net, row['scholarship_calculation']
        assert money(row['scholarship']) == gross - expected_net, row
        passed += 1
        if cid in TIER2:
            absent = dict(eligible)
            absent.pop('arden_country_evidence_accepted')
            row, _ = compare(course, absent)
            assert money(row['scholarship']) == 0, row
            passed += 1
            excluded = {**eligible, 'arden_partner_route': 'yes'}
            row, _ = compare(course, excluded)
            assert money(row['scholarship']) == 0, row
            passed += 1
            ghana = {**eligible, 'residence': 'GH', 'arden_application_residence': 'GH'}
            row, _ = compare(course, ghana)
            assert money(row['net_tuition']) == expected_net, row
            passed += 1
        print(f'PASS {cid}: programme total GBP {gross}; eligible net GBP {expected_net}; annual comparison isolated.')
    course = Course.objects.get(university_id='UK004', course_id=TOPUP)
    base = dict(residence='NG', destination='UK', programme='TOPUP', subject=course.title)
    base.update({key: 'yes' for key in FACTS})
    base.update(arden_partner_route='no', arden_intake='2026-11')
    for campus, standard, combined in [('Manchester', Decimal('12500'), Decimal('12500')),
                                       ('London Stratford', Decimal('13500'), Decimal('10500'))]:
        params = {**base, 'arden_campus': campus}
        row, _ = compare(course, params)
        assert money(row['tuition_fee']) == Decimal('16000'), row
        assert money(row['net_tuition']) == standard, row['scholarship_calculation']
        assert row['deposit_calculation']['rule'] == 'application_advised'
        assert row['tuition_deposit'] is None
        passed += 1
        cutoff = datetime.fromisoformat(DEADLINES['2026-11'] + 'T17:30:00').replace(tzinfo=ZoneInfo('Europe/London')).isoformat()
        row, _ = compare(course, {**params, 'arden_unconditional_offer_at': cutoff, 'arden_deposit_paid_at': cutoff})
        assert money(row['net_tuition']) == combined, row['scholarship_calculation']
        passed += 1
    print('PASS campus top-up: standard offers; London early bird; Manchester discrepancy held; no numeric deposit invented.')
    print('Passed new-route API scenarios:', passed)
    print('No database records changed.')


run()

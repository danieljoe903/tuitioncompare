"""Synthetic read-only API tests after importing the 34 Arden routes."""
import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.arden.route_calculation import ONLINE_FACTS
from app.scraping.uk.arden.calculation import FACTS, DEADLINES
from scripts.universities.uk.arden.import_remaining_routes import IDS, GLOBAL_LEVEL_IDS, ONLINE_TOPUPS, CAMPUS_TOPUPS, ROOT, read


def run():
    factory = RequestFactory(); endpoint = '/api/compare-tuition/'; match = resolve(endpoint)
    count = 0
    plans = read(ROOT / 'data/universities/uk/arden/remaining_34_plan.json')['plans']
    print('Synthetic eligibility only. No real scholarship award is confirmed.')

    def request(course, params):
        response = match.func(factory.get(endpoint, data=params), *match.args, **match.kwargs)
        if hasattr(response, 'render'): response.render()
        assert response.status_code == 200, response.content[:1000]
        data = json.loads(response.content)
        rows = [r for r in data['results'] if r['course_id'] == course.course_id]
        assert len(rows) == 1, (course.course_id, len(rows), data)
        return rows[0], data

    def money(row, key, expected):
        assert row.get(key) is not None and Decimal(str(row[key])).quantize(Decimal('.01')) == Decimal(expected), (row['course_id'], key, row)

    for plan in plans:
        course = Course.objects.get(university_id='UK004', course_id=plan['course_id'])
        params = dict(residence='NG', destination='UK', programme='UG', subject=course.title)
        category = plan['category']; cid = course.course_id
        if category == 'published_fee_unavailable':
            row, _ = request(course, params)
            assert not row['fee_available'] and row['tuition_fee'] is None and row['scholarship'] is None and row['tuition_deposit'] is None, row
            assert 'not available' in row['fee_note'].lower() or 'unavailable' in row['fee_note'].lower(), row
            count += 1
        elif category == 'online_per_level_fee_proposal':
            row, _ = request(course, params)
            assert not row['fee_available'] and 'per level' in row['fee_note'].lower(), row
            count += 1
            eligible = {**params, **{key:'yes' for key in ONLINE_FACTS}, 'arden_partner_route':'no', 'arden_application_residence':'NG'}
            for level in ['4','5','6']:
                row, payload = request(course, {**eligible,'comparison_basis':'per_level','arden_level':level})
                assert row['fee_available'] and row['fee_basis']=='per_level' and row['selected_level']==level, row
                money(row,'tuition_fee','5500.00'); money(row,'scholarship','0.00'); money(row,'net_tuition','5500.00')
                assert all(r.get('fee_basis')=='per_level' for r in payload['results'] if r['fee_available'])
                assert row['deposit_calculation']['rule']=='campus_policy_not_applicable' and row['tuition_deposit'] is None
                count += 1
            row, _ = request(course,{**eligible,'comparison_basis':'per_level'})
            assert not row['fee_available'], row
            count += 1
            row, _ = request(course,{**eligible,'comparison_basis':'per_level','arden_level':'7'})
            assert not row['fee_available'], row
            count += 1
            row, payload = request(course,{**eligible,'comparison_basis':'programme_total'})
            assert row['fee_available'] and row['fee_basis']=='derived_programme_total' and row['programme_total_is_derived'] is True, row
            money(row,'tuition_fee','16500.00')
            net='7425.00' if cid in GLOBAL_LEVEL_IDS else '16500.00'
            money(row,'net_tuition',net)
            assert row['eligibility_questions']['scholarship'], row
            assert all(r.get('comparison_period')=='total_programme' for r in payload['results'] if r['fee_available'])
            count += 1
            row, _ = request(course,{**params,'comparison_basis':'programme_total'})
            money(row,'scholarship','0.00');money(row,'net_tuition','16500.00');count += 1
            row, _ = request(course,{**eligible,'comparison_basis':'programme_total','arden_partner_route':'yes'})
            money(row,'scholarship','0.00');count += 1
            row, _ = request(course,{**eligible,'comparison_basis':'programme_total','residence':'CA'})
            assert not row['fee_available'], row
            count += 1
            row, _ = request(course,{**eligible,'comparison_basis':'programme_total','arden_intake':'2026-10'})
            assert not row['fee_available'], row
            count += 1
            print(f'PASS {cid}: level GBP 5500; derived programme GBP 16500; eligible programme net GBP {net}.')
        elif cid in ONLINE_TOPUPS:
            row, _ = request(course,params)
            assert not row['fee_available'], row
            count += 1
            eligible={**params,**{key:'yes' for key in ONLINE_FACTS},'arden_partner_route':'no','arden_application_residence':'NG','comparison_basis':'programme_total'}
            row, _ = request(course,eligible)
            money(row,'tuition_fee','5500.00');money(row,'scholarship','3025.00');money(row,'net_tuition','2475.00')
            assert row['fee_basis']=='total_programme_fee' and row['tuition_deposit'] is None and row['deposit_calculation']['rule']=='campus_policy_not_applicable'
            count += 1
            row, _ = request(course,{**params,'comparison_basis':'programme_total'})
            money(row,'scholarship','0.00');count += 1
            row, _ = request(course,{**eligible,'arden_intake':'2026-10'})
            assert not row['fee_available'], row
            count += 1
            row, _ = request(course,{**eligible,'residence':'GH','arden_application_residence':'GH'})
            money(row,'net_tuition','2475.00');count += 1
            print(f'PASS {cid}: GBP 5500 - 3025 = 2475 if eligible.')
        else:
            assert cid in CAMPUS_TOPUPS
            eligible={**params,**{key:'yes' for key in FACTS},'arden_partner_route':'no','arden_intake':'2026-11'}
            for location,net,early in [('Manchester','12500.00','12500.00'),('London Stratford','13500.00','10500.00')]:
                selected={**eligible,'arden_campus':location}
                row, _ = request(course,selected)
                money(row,'tuition_fee','16000.00');money(row,'net_tuition',net)
                assert row['deposit_calculation']['rule']=='application_advised' and row['tuition_deposit'] is None
                count += 1
                cutoff=datetime.fromisoformat(DEADLINES['2026-11']+'T17:30:00').replace(tzinfo=ZoneInfo('Europe/London')).isoformat()
                row, _ = request(course,{**selected,'arden_unconditional_offer_at':cutoff,'arden_deposit_paid_at':cutoff})
                money(row,'net_tuition',early);count += 1
                row, _ = request(course,{**params,'arden_campus':location})
                money(row,'scholarship','0.00');count += 1
            print(f'PASS {cid}: standard campus awards, London early bird, Manchester discrepancy held; individual deposit message.')
    print('Passed remaining-route API scenarios:',count)
    print('No database records changed.')


run()

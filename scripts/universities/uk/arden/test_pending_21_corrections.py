"""Read-only synthetic checks after applying the two supported corrections."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.arden.calculation import FACTS


def run():
    duplicate=Course.objects.get(university_id='UK004',course_id='C_UK004_26')
    assert duplicate.is_active is False
    canonical=Course.objects.get(university_id='UK004',course_id='C_ARD004_01')
    assert canonical.is_active is True
    route=Course.objects.get(university_id='UK004',course_id='C_UK004_47')
    assert route.location=='Online'
    factory=RequestFactory();endpoint='/api/compare-tuition/';match=resolve(endpoint)
    def call(params):
        response=match.func(factory.get(endpoint,data=params),*match.args,**match.kwargs)
        if hasattr(response,'render'):response.render()
        assert response.status_code==200,response.content[:1000]
        return json.loads(response.content)['results']
    base=dict(residence='NG',destination='UK',programme='UG',subject='Business Management (Top')
    eligible={**base,**{k:'yes' for k in FACTS},'arden_partner_route':'no','arden_campus':'Manchester','arden_intake':'2026-11'}
    rows=call(eligible)
    assert not any(r['course_id']==duplicate.course_id for r in rows)
    matches=[r for r in rows if r['course_id']==canonical.course_id]
    assert len(matches)==1
    row=matches[0]
    assert row['fee_available'] and Decimal(str(row['tuition_fee']))==16000
    assert Decimal(str(row['scholarship']))==3500 and Decimal(str(row['net_tuition']))==12500
    assert row['tuition_deposit'] is None
    rows=call(dict(residence='NG',destination='UK',programme='UG',subject=route.title))
    matches=[r for r in rows if r['course_id']==route.course_id]
    assert len(matches)==1
    row=matches[0]
    assert not row['fee_available'] and row['tuition_fee'] is None and row['scholarship'] is None
    assert 'intake' in row['fee_note'].lower()
    assert row['eligibility_questions']['scholarship']
    print('PASS: duplicate excluded; canonical top-up remains active.')
    print('PASS: canonical Manchester GBP 16000 - 3500 = 12500 if eligible; individual deposit amount not invented.')
    print('PASS: HRM online route corrected; unknown intake blocks fee comparison and scholarship deduction; questions preserved.')
    print('No database records changed by these synthetic tests.')


run()

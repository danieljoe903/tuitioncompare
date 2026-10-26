"""Synthetic read-only comparison checks for the two accepted replacements."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course, TuitionSource
from app.scraping.uk.arden.route_calculation import ONLINE_FACTS


def run():
    factory=RequestFactory();endpoint='/api/compare-tuition/';match=resolve(endpoint);passed=0
    crim=Course.objects.get(university_id='UK004',course_id='C_UK004_46')
    foundation=Course.objects.get(university_id='UK004',course_id='C_UK004_57')
    assert crim.title=='BA (Hons) Criminology' and crim.location=='Online'
    assert foundation.title=='BSc (Hons) Accounting and Finance with Foundation Year'
    for course in [crim,foundation]:
        source=TuitionSource.objects.get(tuition__course_id=course.course_id,tuition__residency='INTL')
        assert source.raw_fees['mapping']['kind']=='explicit_replacement'
        assert source.raw_fees['mapping']['accepted_on_apply'] is True
    def call(course,params):
        response=match.func(factory.get(endpoint,data=params),*match.args,**match.kwargs)
        if hasattr(response,'render'):response.render()
        assert response.status_code==200,response.content[:1000]
        data=json.loads(response.content);rows=[r for r in data['results'] if r['course_id']==course.course_id]
        assert len(rows)==1,(course.course_id,data)
        return rows[0]
    def amount(row,key,expected):
        assert row.get(key) is not None and Decimal(str(row[key]))==Decimal(expected),(key,row)
    base=dict(residence='NG',destination='UK',programme='UG',subject=crim.title)
    row=call(crim,base);assert not row['fee_available'] and 'per level' in row['fee_note'].lower();passed+=1
    for level in ['4','5','6']:
        row=call(crim,{**base,'comparison_basis':'per_level','arden_level':level})
        assert row['fee_available'] and row['fee_basis']=='per_level' and row['selected_level']==level
        amount(row,'tuition_fee','5500');amount(row,'scholarship','0');amount(row,'net_tuition','5500')
        assert row['tuition_deposit'] is None and row['deposit_calculation']['rule']=='campus_policy_not_applicable'
        passed+=1
    row=call(crim,{**base,'comparison_basis':'programme_total'})
    assert row['fee_available'] and row['programme_total_is_derived'] is True and row['fee_basis']=='derived_programme_total'
    amount(row,'tuition_fee','16500');amount(row,'net_tuition','16500');amount(row,'scholarship','0');passed+=1
    eligible={**base,**{k:'yes' for k in ONLINE_FACTS},'arden_partner_route':'no','arden_application_residence':'NG','comparison_basis':'programme_total'}
    row=call(crim,eligible);amount(row,'scholarship','0');amount(row,'net_tuition','16500');passed+=1
    row=call(crim,{**eligible,'residence':'GH','arden_application_residence':'GH'});assert row['fee_available'];amount(row,'net_tuition','16500');passed+=1
    for change in [{'residence':'CA'},{'arden_intake':'2027-04'},{'arden_campus':'Manchester'}]:
        row=call(crim,{**eligible,**change});assert not row['fee_available'],row;passed+=1
    for change in [{},{'arden_level':'7'}]:
        row=call(crim,{**base,'comparison_basis':'per_level',**change});assert not row['fee_available'],row;passed+=1
    row=call(foundation,dict(residence='NG',destination='UK',programme='UG',subject=foundation.title))
    assert not row['fee_available'] and row['tuition_fee'] is None and row['scholarship'] is None and row['tuition_deposit'] is None
    assert 'foundation' in row['fee_note'].lower();passed+=1
    print('PASS: explicit replacements recorded; Criminology level fees and derived programme total separated from annual fees.')
    print('PASS: no unsupported Criminology scholarship deduction or deposit assigned, including with synthetic eligibility answers.')
    print('PASS: country/campus/intake/level guards; foundation fee remains unavailable.')
    print('Passed two-replacement API scenarios:',passed)
    print('No database records changed by these tests; no real eligibility confirmed.')


run()

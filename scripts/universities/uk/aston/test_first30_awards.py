"""Actual endpoint, synthetic saved applicant facts only. No database writes."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.aston.calculation import manifest
from app.scraping.uk.aston.awards import COMMON,FACTS,EXCLUDED,specs
factory=RequestFactory();endpoint='/api/compare-tuition/';match=resolve(endpoint);passed=0
def compare(c,answers=None,year=None,query=None):
    params={'destination':'UK','programme':c.level,'subject':c.title}
    if query:params.update(query)
    req=factory.get(endpoint,data=params);req.session={}
    if answers is not None:
        req.session={'scholarship_eligibility':{'UK007':{c.course_id:{'academic_year':year or manifest()[c.course_id]['academic_year'],'answers':answers}}}}
    response=match.func(req,*match.args,**match.kwargs)
    if hasattr(response,'render'):response.render()
    assert response.status_code==200,response.content[:500]
    rows=[r for r in json.loads(response.content)['results'] if r['course_id']==c.course_id]
    assert len(rows)==1,(c.course_id,rows)
    return rows[0]
def check(row,amount,gross):
    assert Decimal(str(row['scholarship']))==amount,row
    assert Decimal(str(row['net_tuition']))==gross-amount,row
    assert row['tuition_deposit'] is None,row
    assert row['eligibility_questions']['scholarship']==[],row
print('Synthetic saved facts only. No real award, enrolment or payment is confirmed; no database writes.')
for cid,m in sorted(manifest().items()):
    c=Course.objects.get(university_id='UK007',course_id=cid);gross=Decimal(m['tuition_fee'])
    all_yes={k:True for k in FACTS}
    check(compare(c),Decimal(0),gross);passed+=1
    check(compare(c,all_yes,year='2030/31'),Decimal(0),gross);passed+=1
    check(compare(c,query={k:'true' for k in FACTS}),Decimal(0),gross);passed+=1
    if cid in EXCLUDED:
        check(compare(c,all_yes),Decimal(0),gross);passed+=1
        print('PASS',cid,': excluded from reviewed Community/Cadbury scopes.');continue
    community={**all_yes,'aston_cadbury_application_accepted':False,'aston_cadbury_awarded_applied':False}
    value=Decimal('3000') if c.level=='UG' else Decimal('4000') if m['start_month']=='January' else Decimal('5500')
    check(compare(c,community),value,gross);passed+=1
    # Both eligible offers select Cadbury, without stacking.
    check(compare(c,all_yes),Decimal('10000'),gross);passed+=1
    cadbury={**all_yes,'aston_ics_applied':False,'aston_ug_direct_nonpartner':False,'aston_pg_no_dedicated_partner_award':False}
    check(compare(c,cadbury),Decimal('10000'),gross);passed+=1
    for key in COMMON:
        missing=dict(community);missing.pop(key)
        check(compare(c,missing),Decimal(0),gross);passed+=1
        check(compare(c,{**community,key:False}),Decimal(0),gross);passed+=1
    for key in ['aston_cadbury_application_accepted','aston_cadbury_awarded_applied']:
        check(compare(c,{**cadbury,key:False}),Decimal(0),gross);passed+=1
    print('PASS',cid,': Community net',gross-value,'; Cadbury net',gross-Decimal('10000'),'; missing/negative facts, cohort, query and stacking guards.')
print('Passed Aston award API scenarios:',passed)
print('No database records changed.')

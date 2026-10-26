"""Synthetic saved eligibility through the actual comparison endpoint. No DB writes."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.aub.calculation import FACTS
from app.scraping.uk.aub.remaining_calculation import ONLINE_IDS,FOUNDATION_IDS,PROGRESSION_FACT
factory=RequestFactory();route=resolve('/api/compare-tuition/');passed=0


def compare(c,answers=None,year='2027/28',extra=None,basis='programme_total'):
    params={'destination':'UK','programme':c.level,'subject':c.title,'comparison_basis':basis}
    if extra:params.update(extra)
    req=factory.get('/api/compare-tuition/',data=params);req.session={}
    if answers is not None:req.session={'scholarship_eligibility':{'UK005':{c.course_id:{'academic_year':year,'answers':answers}}}}
    r=route.func(req,*route.args,**route.kwargs)
    if hasattr(r,'render'):r.render()
    assert r.status_code==200,r.content[:300]
    rows=[x for x in json.loads(r.content)['results'] if x['course_id']==c.course_id];assert len(rows)==1,rows
    return rows[0]


print('Synthetic progression facts only; no real award confirmed and no database writes.')
for cid in sorted(ONLINE_IDS):
    c=Course.objects.get(university_id='UK005',course_id=cid)
    yes={**{k:True for k in FACTS},PROGRESSION_FACT:True}
    r=compare(c,yes);assert Decimal(str(r['scholarship']))==1995 and Decimal(str(r['net_tuition']))==7980,r
    assert Decimal(str(r['tuition_deposit']))==500 and r['deposit_calculation']['included_in_tuition'] is True,r
    assert len(r['scholarship_calculation']['applied'])==1 and r['eligibility_questions']=={'scholarship':[],'deposit':[]},r;passed+=1
    for key in yes:
        absent=dict(yes);absent.pop(key)
        for facts in [absent,{**yes,key:False}]:
            assert Decimal(str(compare(c,facts)['scholarship']))==0,(cid,key);passed+=1
    assert Decimal(str(compare(c,yes,year='2026/27')['scholarship']))==0;passed+=1
    assert Decimal(str(compare(c,extra={k:'true' for k in yes})['scholarship']))==0;passed+=1
    assert Decimal(str(compare(c,{**yes,'aub_confirmed_award_amount':'5000.00'})['scholarship']))==1995;passed+=1
    print('PASS',cid,': GBP 9,975 - GBP 1,995 = GBP 7,980; missing/negative facts and wrong cohort block deduction.')
for cid in sorted(FOUNDATION_IDS):
    c=Course.objects.get(university_id='UK005',course_id=cid)
    r=compare(c,{**{k:True for k in FACTS},PROGRESSION_FACT:True,'aub_confirmed_award_amount':'5000.00'},basis='annual')
    assert r['fee_available'] is True and Decimal(str(r['scholarship']))==0 and Decimal(str(r['net_tuition']))==19950 and r['published_tuition']['provisional'] is False,r;passed+=1
print('Passed online/foundation award API scenarios:',passed)
print('No database records changed.')

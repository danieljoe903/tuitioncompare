"""Actual comparison endpoint tests after import. No database writes."""
import json
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.aub.calculation import FACTS

factory=RequestFactory();endpoint='/api/compare-tuition/';match=resolve(endpoint)
manifest=json.loads((Path(settings.BASE_DIR)/'data/universities/uk/aub/first_30_reviewed_routes.json').read_text(encoding='utf-8'))
passed=0


def compare(course,answers=None,residence='',year='2027/28',extra=None):
    params=dict(destination='UK',programme=course.level,subject=course.title)
    if residence:params['residence']=residence
    if extra:params.update(extra)
    request=factory.get(endpoint,data=params)
    request.session={}
    if answers is not None:request.session={'scholarship_eligibility':{'UK005':{course.course_id:{'academic_year':year,'answers':answers}}}}
    response=match.func(request,*match.args,**match.kwargs)
    if hasattr(response,'render'):response.render()
    assert response.status_code==200,response.content[:500]
    rows=[r for r in json.loads(response.content)['results'] if r['course_id']==course.course_id]
    assert len(rows)==1,(course.course_id,len(rows))
    return rows[0]


def money(v):return Decimal(str(v)).quantize(Decimal('.01'))


print('Synthetic saved scholarship answers only. No real award confirmed; no DB writes.')
for item in manifest:
    c=Course.objects.get(university_id='UK005',course_id=item['course_id'])
    for residence in ['', 'NG']:
        row=compare(c,residence=residence)
        assert row['fee_available'] is True and row['fee_verified'] is True,row
        assert money(row['tuition_fee'])==Decimal('19950.00') and money(row['scholarship'])==0,row
        assert money(row['net_tuition'])==Decimal('19950.00') and row['tuition_deposit'] is None,row
        assert row['eligibility_questions']['scholarship']==[],row
        passed+=1
    answers={**{k:True for k in FACTS},'aub_confirmed_award_amount':'1250.00'}
    row=compare(c,answers)
    assert money(row['scholarship'])==Decimal('1250.00') and money(row['net_tuition'])==Decimal('18700.00'),row
    passed+=1
    maximum='5000.00' if c.level=='UG' else '3000.00'
    row=compare(c,{**answers,'aub_confirmed_award_amount':maximum})
    assert money(row['scholarship'])==Decimal(maximum),row
    passed+=1
    for bad in ['', '-1', 'NaN','Infinity','99999','0','1000.001']:
        assert money(compare(c,{**answers,'aub_confirmed_award_amount':bad})['scholarship'])==0,(c.course_id,bad)
        passed+=1
    for key in FACTS:
        absent=dict(answers);absent.pop(key)
        assert money(compare(c,absent)['scholarship'])==0,(c.course_id,key)
        assert money(compare(c,{**answers,key:False})['scholarship'])==0,(c.course_id,key)
        passed+=2
    assert money(compare(c,answers,year='2026/27')['scholarship'])==0
    assert money(compare(c,extra={**{k:'yes' for k in FACTS},'aub_confirmed_award_amount':'5000'})['scholarship'])==0
    passed+=2
    if c.course_id=='C_UK005_39':assert row['course_availability_status']=='subject_to_validation'
    print(c.course_id+': blank residence, saved exact amounts, missing/negative facts and pending deposit passed.')
print('Passed AUB comparison API scenarios:',passed)
print('No database records changed.')

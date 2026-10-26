"""Real endpoint checks with synthetic answers only; no database writes."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.aub.calculation import FACTS
factory=RequestFactory();route=resolve('/api/compare-tuition/');passed=0


def compare(c,answers=None):
    r=factory.get('/api/compare-tuition/',data={'destination':'UK','programme':'UG','subject':c.title});r.session={}
    if answers is not None:r.session={'scholarship_eligibility':{'UK005':{c.course_id:{'academic_year':'2027/28','answers':answers}}}}
    response=route.func(r,*route.args,**route.kwargs)
    if hasattr(response,'render'):response.render()
    assert response.status_code==200,response.content[:300]
    rows=[x for x in json.loads(response.content)['results'] if x['course_id']==c.course_id];assert len(rows)==1,rows
    return rows[0]


print('Synthetic answers only; no real award confirmed or database writes.')
for n in range(43,51):
    c=Course.objects.get(university_id='UK005',course_id=f'C_UK005_{n:02d}');r=compare(c)
    assert r['fee_available'] and r['fee_verified'] and Decimal(str(r['tuition_fee']))==19950,r
    assert Decimal(str(r['net_tuition']))==19950 and Decimal(str(r['scholarship']))==0 and r['tuition_deposit'] is None,r
    assert r['published_tuition']['provisional'] is False,r;passed+=1
    r=compare(c,{**{k:True for k in FACTS},'aub_confirmed_award_amount':'5000.00'})
    assert Decimal(str(r['scholarship']))==0 and Decimal(str(r['net_tuition']))==19950,r;passed+=1
    print('PASS',c.course_id,': verified GBP 19,950 tuition; Year 0 deduction still blocked.')
c=Course.objects.get(university_id='UK005',course_id='C_UK005_11');r=compare(c)
assert c.ucas_cao_code=='W713' and c.start_year==2027 and c.duration=='3 Years'
assert r['fee_available'] and Decimal(str(r['tuition_fee']))==19950 and Decimal(str(r['scholarship']))==0;passed+=1
facts={**{k:True for k in FACTS},'aub_confirmed_award_amount':'1250.00'}
r=compare(c,facts);assert Decimal(str(r['scholarship']))==1250 and Decimal(str(r['net_tuition']))==18700;passed+=1
for key in FACTS:
    absent=dict(facts);absent.pop(key)
    assert Decimal(str(compare(c,absent)['scholarship']))==0;passed+=1
assert Decimal(str(compare(c,{**facts,'aub_confirmed_award_amount':'5000.01'})['scholarship']))==0;passed+=1
print('PASS Dance: exact route, granted amount, missing facts and published maximum guards.')
print('Passed foundation/Dance API scenarios:',passed)
print('No database records changed.')

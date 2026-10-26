"""Run against the real comparison endpoint after import; no database writes."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course,Tuition
from app.scraping.uk.aub.calculation import FACTS

factory=RequestFactory();route=resolve('/api/compare-tuition/');passed=0


def request_rows(c,basis='annual',answers=None,residence=''):
    params={'destination':'UK','programme':c.level,'subject':c.title,'comparison_basis':basis}
    if residence:params['residence']=residence
    request=factory.get('/api/compare-tuition/',data=params);request.session={}
    if answers is not None:request.session={'scholarship_eligibility':{'UK005':{c.course_id:{'academic_year':'2027/28','answers':answers}}}}
    response=route.func(request,*route.args,**route.kwargs)
    if hasattr(response,'render'):response.render()
    assert response.status_code==200,response.content[:300]
    return [r for r in json.loads(response.content)['results'] if r['course_id']==c.course_id]


def one(c,**kw):
    rows=request_rows(c,**kw);assert len(rows)==1,(c.course_id,rows);return rows[0]


def amount(v):return Decimal(str(v)).quantize(Decimal('.01'))


print('Synthetic answers only; no real award confirmation or database writes.')
for cid in ['C_UK005_23','C_UK005_40','C_UK005_41','C_UK005_42']:
    c=Course.objects.get(university_id='UK005',course_id=cid)
    for residence in ['', 'NG']:
        r=one(c,basis='programme_total',residence=residence)
        assert r['fee_available'] and r['fee_verified'] and amount(r['tuition_fee'])==9975 and amount(r['net_tuition'])==9975 and amount(r['scholarship'])==0,r
        assert r['comparison_period']=='total_programme' and r['fee_basis']=='total_programme_fee',r
        assert amount(r['tuition_deposit'])==500 and r['deposit_calculation']['included_in_tuition'] is True and r['deposit_calculation']['is_cas_deposit'] is False,r
        assert r['eligibility_questions']=={'scholarship':[],'deposit':[]},r
        passed+=1
    r=one(c);assert r['fee_available'] is False and r['tuition_fee'] is None and r['published_tuition']['amount']=='9975.00',r;passed+=1
    facts={**{k:True for k in FACTS},'aub_confirmed_award_amount':'3000.00'}
    r=one(c,basis='programme_total',answers=facts);assert amount(r['scholarship'])==0 and amount(r['net_tuition'])==9975,r;passed+=1
    assert request_rows(c,basis='per_level')==[];passed+=1
    print('PASS',cid,': programme price, acceptance allocation, blank residence and no unsupported award.')
for n in range(43,51):
    c=Course.objects.get(university_id='UK005',course_id=f'C_UK005_{n:02d}')
    r=one(c);assert r['fee_available'] is True and r['fee_verified'] is True and amount(r['tuition_fee'])==19950 and amount(r['scholarship'])==0,r
    assert r['published_tuition']['amount']=='19950.00' and r['published_tuition']['provisional'] is False and r['tuition_deposit'] is None,r;passed+=1
    r=one(c,answers={**{k:True for k in FACTS},'aub_confirmed_award_amount':'5000.00'});assert amount(r['scholarship'])==0 and amount(r['net_tuition'])==19950,r;passed+=1
    assert request_rows(c,basis='programme_total')==[];passed+=1
    print('PASS',c.course_id,': direct AUB foundation fee confirmed; unconfirmed Year 0 award blocked.')
c=Course.objects.get(university_id='UK005',course_id='C_UK005_33');assert c.title=='MA Fashion Innovation'
r=one(c);assert r['fee_available'] is True and amount(r['tuition_fee'])==19950 and amount(r['scholarship'])==0 and r['tuition_deposit'] is None,r;passed+=1
facts={**{k:True for k in FACTS},'aub_confirmed_award_amount':'1250.00'}
r=one(c,answers=facts);assert amount(r['scholarship'])==1250 and amount(r['net_tuition'])==18700,r;passed+=1
for key in FACTS:
    missing=dict(facts);missing.pop(key)
    assert amount(one(c,answers=missing)['scholarship'])==0;passed+=1
for bad in ['3000.01','NaN','0','-100']:
    assert amount(one(c,answers={**facts,'aub_confirmed_award_amount':bad})['scholarship'])==0;passed+=1
print('PASS Fashion: exact award amount only; missing facts, invalid amounts and unsupported deposit guarded.')
print('Passed new-route API scenarios:',passed)
print('No database records changed.')

"""Live fee/route checks; new award scopes remain deliberately pending."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course,Tuition,TuitionSource
from app.scraping.uk.aston.calculation import all_fee_manifest,manifest,fee_error
from app.scraping.uk.aston.awards import FACTS,EXCLUDED
factory=RequestFactory();endpoint='/api/compare-tuition/';match=resolve(endpoint);passed=0
for cid,m in sorted(all_fee_manifest().items()):
    if cid in manifest():continue
    c=Course.objects.get(university_id='UK007',course_id=cid)
    for answers in [None,{k:True for k in FACTS}]:
        request=factory.get(endpoint,data={'destination':'UK','programme':c.level,'subject':c.title});request.session={}
        if answers is not None:request.session={'scholarship_eligibility':{'UK007':{cid:{'academic_year':m['academic_year'],'answers':answers}}}}
        response=match.func(request,*match.args,**match.kwargs)
        if hasattr(response,'render'):response.render()
        assert response.status_code==200,response.content[:500]
        rows=[r for r in json.loads(response.content)['results'] if r['course_id']==cid]
        assert len(rows)==1,(cid,rows)
        row=rows[0]
        assert row['fee_verified'] and row['fee_available'],row
        assert Decimal(str(row['tuition_fee']))==Decimal(m['tuition_fee']),row
        assert row['academic_year']==m['academic_year'] and row['fee_basis']==m['fee_basis'],row
        deduction=Decimal('10000') if answers is not None and cid not in EXCLUDED else Decimal('0')
        assert Decimal(str(row['scholarship']))==deduction and Decimal(str(row['net_tuition']))==Decimal(m['tuition_fee'])-deduction,row
        assert row['tuition_deposit'] is None,row
        assert row['deposit_calculation']['rule']=='offer_letter',row
        assert 'offer-letter specific' in row['deposit_calculation']['note'],row
        passed+=1
    print('PASS',cid,': fee/year/basis and blank residence; extended scopes calculate only confirmed awards; deposit is offer-letter specific.')
opt=Course.objects.get(university_id='UK007',course_id='C_UK007_32')
assert opt.title=='MOptom Optometry'
t=Tuition.objects.get(course=opt,residency='INTL');s=TuitionSource.objects.get(tuition=t)
history=s.raw_fees['replacement_history']
assert history['previous_title']=='BSc (Hons) Optometry' and history['accepted'] is True
duplicate=Course.objects.get(university_id='UK007',course_id='C_UK007_50')
assert duplicate.is_active,'MBA duplicate is awaiting explicit review, not retired by this import.'
print('PASS: explicit Optometry history retained; second MBA left for duplicate review.')
print('Passed new-route API scenarios:',passed)
print('No database records changed. Existing first30 rules remain covered in the extended scope.')

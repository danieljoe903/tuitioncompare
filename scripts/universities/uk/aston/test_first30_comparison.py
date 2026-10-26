"""Read-only live endpoint checks after import; no synthetic award enabled yet."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.aston.calculation import manifest
factory=RequestFactory();endpoint='/api/compare-tuition/';match=resolve(endpoint);passed=0
for cid,m in sorted(manifest().items()):
    c=Course.objects.get(university_id='UK007',course_id=cid)
    req=factory.get(endpoint,data={'destination':'UK','programme':c.level,'subject':c.title});req.session={}
    response=match.func(req,*match.args,**match.kwargs)
    if hasattr(response,'render'):response.render()
    assert response.status_code==200,response.content[:500]
    rows=[r for r in json.loads(response.content)['results'] if r['course_id']==cid]
    assert len(rows)==1,(cid,rows)
    r=rows[0]
    assert r['fee_available'] and r['fee_verified'],r
    assert Decimal(str(r['tuition_fee']))==Decimal(m['tuition_fee']),r
    assert r['academic_year']==m['academic_year'],r
    assert r['fee_basis']==m['fee_basis'],r
    assert Decimal(str(r['scholarship']))==0,r
    assert Decimal(str(r['net_tuition']))==Decimal(m['tuition_fee']),r
    assert r['tuition_deposit'] is None,r
    assert r['eligibility_questions']['scholarship']==[],r
    passed+=1;print('PASS',cid,': verified fee/year/basis; no unverified deduction/deposit; blank residence retained.')
print('Passed Aston comparison scenarios:',passed)
print('No database records changed. Scholarship/deposit rules require separate reconciliation.')

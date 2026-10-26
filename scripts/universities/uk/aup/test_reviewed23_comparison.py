"""Actual comparison endpoint, synthetic saved facts, no DB writes."""
import json
from decimal import Decimal
from django.test import RequestFactory
from django.urls import resolve
from app.models import Course
from app.scraping.uk.aup.calculation import IDS,PG_IDS,FACTS,VISA
factory=RequestFactory();endpoint='/api/compare-tuition/';match=resolve(endpoint);passed=0

def money(x):return Decimal(str(x)).quantize(Decimal('.01'))
def compare(c,answers=None,year='2027/28',extra=None,expect_present=True):
    params={'destination':'UK','programme':c.level,'subject':c.title}
    if extra:params.update(extra)
    req=factory.get(endpoint,data=params);req.session={}
    if answers is not None:req.session={'scholarship_eligibility':{'UK006':{c.course_id:{'academic_year':year,'answers':answers}}}}
    response=match.func(req,*match.args,**match.kwargs)
    if hasattr(response,'render'):response.render()
    assert response.status_code==200,response.content[:500]
    rows=[r for r in json.loads(response.content)['results'] if r['course_id']==c.course_id]
    if not expect_present:
        assert rows == [], (c.course_id, rows)
        return None
    assert len(rows) == 1, (c.course_id, rows)
    return rows[0]

print('Synthetic Alumni/visa answers only. No real award/payment confirmed; no database writes.')
for cid in sorted(IDS):
    c=Course.objects.get(university_id='UK006',course_id=cid)
    expected=Decimal('17500.00') if cid in {'C_UK006_15','C_UK006_16'} else Decimal('18000.00')
    base=compare(c)
    assert base['fee_available'] and base['fee_verified'] and money(base['tuition_fee'])==expected,base
    assert money(base['scholarship'])==0 and money(base['net_tuition'])==expected and base['tuition_deposit'] is None,base
    assert base['eligibility_questions']['scholarship']==[],base
    passed+=1
    row=compare(c,{VISA:True})
    assert money(row['tuition_deposit'])==2000 and row['deposit_calculation']['included_in_tuition'] is True,row
    assert money(row['net_tuition'])==expected,row;passed+=1
    assert compare(c,{VISA:False})['tuition_deposit'] is None;passed+=1
    facts={**{k:True for k in FACTS},VISA:True}
    row=compare(c,facts)
    discount=Decimal('2700.00') if cid in PG_IDS else Decimal('0.00')
    assert money(row['scholarship'])==discount and money(row['net_tuition'])==expected-discount,row
    assert money(row['tuition_deposit'])==2000,row;passed+=1
    assert money(compare(c,facts,year='2026/27')['scholarship'])==0;passed+=1
    # Comparison query parameters cannot act as stored university confirmation.
    row=compare(c,extra={**{k:'true' for k in FACTS},VISA:'true'})
    assert money(row['scholarship'])==0 and row['tuition_deposit'] is None,row;passed+=1
    if cid in PG_IDS:
        for key in FACTS:
            absent=dict(facts);absent.pop(key)
            assert money(compare(c,absent)['scholarship'])==0;passed+=1
            assert money(compare(c,{**facts,key:False})['scholarship'])==0;passed+=1
    print('PASS',cid,': tuition, conditional scholarship and deposit guards.')
# These two active routes still have unverified tuition.
for cid in ['C_AUP006_01', 'C_UK006_18']:
    c = Course.objects.get(
        university_id='UK006',
        course_id=cid,
    )
    assert c.is_active, cid

    row = compare(
        c,
        {**{k: True for k in FACTS}, VISA: True},
    )
    assert row['fee_available'] is False, row
    assert row['tuition_fee'] is None, row
    assert row['tuition_deposit'] is None, row
    passed += 1

# Historical MA Painting must be excluded from current results.
painting = Course.objects.get(
    university_id='UK006',
    course_id='C_UK006_24',
)
assert painting.is_active is False
compare(painting, expect_present=False)
passed += 1

# Its canonical MA Fine Art route must remain available.
fine_art = Course.objects.get(
    university_id='UK006',
    course_id='C_UK006_17',
)
assert fine_art.is_active is True

row = compare(fine_art)
assert row['fee_available'] is True, row
assert money(row['tuition_fee']) == Decimal('18000.00'), row
passed += 1

print('PASS: historical Painting excluded; canonical Fine Art available.')
print('Passed AUP comparison API scenarios:', passed)
print('No database records changed.')
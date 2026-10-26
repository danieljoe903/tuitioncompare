"""Run through manage.py shell after the reviewed import."""
from decimal import Decimal
from app.models import Tuition, TuitionSource
from app.scraping.uk.aston.calculation import fee_error, fee_metadata, calculate, pending_fee_manifest
count=0
for cid,m in pending_fee_manifest().items():
    t=Tuition.objects.get(course_id=cid,residency='INTL')
    source=TuitionSource.objects.get(tuition=t)
    t.source_metadata=source
    assert fee_error(t,source) is None, cid
    assert t.course.start_month==m['start_month'] and source.academic_year==m['academic_year'],cid
    assert Decimal(t.tuition_fee)==Decimal(m['tuition_fee']),cid
    result=calculate(t,[],{},academic_year=m['academic_year'],study_year=1)
    assert Decimal(result['discount'])==0 and Decimal(result['net'])==Decimal(m['tuition_fee']),cid
    if cid=='C_UK007_26':
        assert fee_metadata(source,source.raw_fees)['comparison_period']=='full_time_course'
    print('PASS',cid,': fee, intake, source and no unconfirmed deduction.')
    count+=1
assert count==5
print('Passed reviewed-route checks: 5. No database records changed.')

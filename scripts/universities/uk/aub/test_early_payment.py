"""Read-only tests of the installed adapter and saved rules; synthetic facts."""
from copy import deepcopy
from app.models import Tuition, Scholarship
from app.scraping.uk.aub.remaining_calculation import calculate, saved_profile
from app.scraping.uk.aub.early_payment import SCOPES, FACTS, DATE_KEY
from types import SimpleNamespace

awards=list(Scholarship.objects.filter(university_id='UK005'))
passed=0
for cid in sorted(set.union(*SCOPES.values())):
    tuition=Tuition.objects.get(course_id=cid,residency='INTL')
    profile={k:True for k in FACTS}
    profile[DATE_KEY]='2026-10-01'
    def run(p, year='2027/28'):
        return calculate(tuition,awards,p,academic_year=year,study_year=1,allow_unverified_legacy=False)
    eligible=run(profile)
    assert eligible['discount']=='1500.00' and eligible['net']=='18450.00', (cid,eligible)
    passed+=1
    for key in FACTS:
        for value in (None,False):
            p=deepcopy(profile)
            if value is None:p.pop(key)
            else:p[key]=value
            assert run(p)['discount']=='0.00', (cid,key,value)
            passed+=1
    for value in ('','bad-date','2027-08-02'):
        p=dict(profile);p[DATE_KEY]=value
        assert run(p)['discount']=='0.00',(cid,value)
        passed+=1
    p=dict(profile,aub_award_granted=True)
    assert run(p)['discount']=='0.00',cid
    passed+=1
    assert run(profile,'2026/27')['discount']=='0.00',cid
    passed+=1
    request=SimpleNamespace(session={'scholarship_eligibility':{'UK005':{cid:{'academic_year':'2027/28','answers':profile}}}})
    assert saved_profile(request,cid,'2027/28')[DATE_KEY]=='2026-10-01'
    passed+=1
print('Passed synthetic waiver adapter scenarios:',passed)
print('No real payment or award confirmed. No database records changed.')

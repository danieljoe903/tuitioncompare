"""Offline report/count/register preservation checks; no Django or DB needed."""
from pathlib import Path
import importlib.util
import json

ROOT=Path(__file__).resolve().parents[4]
path=ROOT/'app/scraping/uk/aub/handoff.py'
spec=importlib.util.spec_from_file_location('aub_handoff_checks',path)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
rows=[]
for n in range(50):
    verified=n<44;online=40<=n<44;foundation=32<=n<40
    rows.append(dict(course_id=str(n),title='Synthetic course',fee_verified=verified,
        fee_basis='total_programme_fee' if online else 'annual_tuition',academic_year='2027/28',
        conditional_award_rule_ids=['rule'] if verified else [],
        award_kinds=['confirmed_amount'] if n<32 else ['fixed'] if verified else [],
        early_payment_scope=verified and n<40 and n!=21,
        online_progression_scope=online,foundation_year_zero=foundation,
        payment=dict(available=online,is_cas_deposit=False),fee_reason='Pending synthetic route' if not verified else None,next_action='Confirm intake'))
s=h.summarize(rows,50,50,[],[])
assert s['verified_international_fees']==44
assert s['pending_international_fees']==6
assert s['verified_fee_bases']=={'annual_tuition':40,'total_programme_fee':4}
assert s['verified_fee_courses_with_conditional_award_scope']==44
assert s['verified_fee_courses_with_actual_amount_award_scope']==32
assert s['verified_fee_courses_with_early_payment_waiver_scope']==39
assert s['verified_fee_courses_with_fixed_online_progression_scope']==4
assert s['verified_fee_courses_with_numeric_payment_available']==4
assert s['verified_fee_courses_with_confirmed_numeric_campus_cas_deposit']==0
assert s['verified_fee_courses_without_verified_numeric_payment']==40
assert s['foundation_year_zero_competitive_scholarship_pending']==8
report=dict(summary=s,generated_at='2026-10-24T00:00:00+00:00',pending_fee_cases=rows[44:],audit_path='logs/example.json',next_university_id='UK006',anomalies=[])
prior=(ROOT/'data/universities/progress/aub_prior_register_reference.md').read_text(encoding='utf-8')
merged=h.merge_register(prior,report)
assert h.merge_register(merged,report)==merged
assert merged.count(h.START)==1 and merged.count(h.END)==1
# Compare each of the four earlier university narratives byte for byte.
for name in h.PRIOR_NAMES:
    assert name in merged
    start=prior.index('## UK — '+name)
    next_heading=prior.find('\n## ',start+1)
    block=prior[start:next_heading if next_heading!=-1 else len(prior)]
    assert block in merged,name
assert '<!-- university-audit:UK005:first30:start -->' in merged
assert '| UK005 | Arts University Bournemouth | Reviewed with outstanding cases; see final AUB audit |' in merged
try:h.merge_register('partial register',report)
except ValueError:pass
else:raise AssertionError('Partial register must be rejected.')
try:h.merge_register(prior+'\n'+h.START,report)
except ValueError:pass
else:raise AssertionError('Malformed markers must be rejected.')
print('PASS: AUB coverage aggregation; register idempotency; all four prior narratives preserved; malformed/partial registers rejected.')
print('Offline synthetic report checks only. No database records changed.')

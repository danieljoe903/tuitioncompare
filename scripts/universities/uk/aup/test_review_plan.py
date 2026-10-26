"""Read-only tests against an exported source review; no Django/network/DB."""
import argparse,importlib.util,json
from copy import deepcopy
from pathlib import Path
root=Path(__file__).resolve().parents[4]
p=root/'app/scraping/uk/aup/review.py';spec=importlib.util.spec_from_file_location('aup_review_check',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);args=parser.parse_args()
d=json.loads(Path(args.evidence).read_text(encoding='utf-8'));manifest=json.loads((root/'data/universities/uk/aup/reviewed_23_routes.json').read_text())
# This string isolates the pure planner; the command fetches the actual current application page separately.
application='September 2027 Undergraduate BA (Hons) Postgraduate Masters Degrees'
r=m.plan(d,manifest,application)
assert len(r['proposals'])==23 and not r['blocked']
assert sum(x['qualification']=='BA (Hons)' for x in r['proposals'])==14
assert sum(x['qualification']=='CertHE' for x in r['proposals'])==2
assert sum(x['qualification']=='MA' for x in r['proposals'])==7
assert all(x['tuition_fee']==('17500.00' if x['qualification']=='CertHE' else '18000.00') for x in r['proposals'])
assert {x['course_id'] for x in r['pending']}=={'C_AUP006_01','C_UK006_18','C_UK006_24'}
assert not r['database_changes'] and not r['mappings_applied']
assert all(x['proposed_course']['duration']==('3 Years' if x['qualification']=='BA (Hons)' else '1 Year') for x in r['proposals'])
try:m.plan(d,manifest,'September 2026 Undergraduate BA Postgraduate Masters')
except ValueError:pass
else:raise AssertionError('Wrong recruitment cycle must block.')
changed=deepcopy(d)
for p in changed['pages']:
 if p['source_url']==m.PG:p['text']=p['text'].replace('£18,000','£19,999')
try:m.plan(changed,manifest,application)
except ValueError:pass
else:raise AssertionError('Conflicting PG fee must block.')
changed=deepcopy(manifest);changed[0]['source_sha256']='not-the-reviewed-hash'
assert len(m.plan(d,changed,application)['blocked'])==1
print('PASS: 23 exact-route plans, qualification-specific fees/durations, three retained gaps, wrong-cohort, fee-conflict and changed-source guards.')
print('Synthetic application text used for planner checks only. The command captures the live application page. No database changes.')

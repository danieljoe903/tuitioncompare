import argparse,json,runpy
from copy import deepcopy
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[4]
n=runpy.run_path(str(root/'app/scraping/uk/aston/remaining25.py'))
data=json.loads(Path(args.evidence).read_text(encoding='utf-8-sig'))
manifest=json.loads((root/'data/universities/uk/aston/reviewed_remaining14.json').read_text())
r=n['plan'](data,manifest)
assert len(r['proposals'])==14 and not r['blocked'],r['blocked']
assert len(n['PENDING'])==11
assert {x['course_id'] for x in r['proposals']}.isdisjoint(n['PENDING'])
opt=next(x for x in r['proposals'] if x['course_id']=='C_UK007_32')
assert opt['proposed_course']['title']=='MOptom Optometry' and opt['mapping']=='explicit_replacement_proposal'
med=next(x for x in r['proposals'] if x['course_id']=='C_UK007_17')
assert med['tuition_fee']=='47000.00' and med['fee_basis']=='annual_tuition'
bad=deepcopy(manifest);bad[0]['source_sha256']='tampered';assert n['plan'](data,bad)['blocked']
bad=deepcopy(manifest);bad[0]['academic_year']='2030/31';assert n['plan'](data,bad)['blocked']
print('PASS: 14 proposals, 11 held; explicit award change; Medicine annual basis; hash/cohort guards. No DB writes.')

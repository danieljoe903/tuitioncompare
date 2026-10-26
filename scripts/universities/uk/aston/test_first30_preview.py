"""Offline planner checks against your saved evidence; no database writes."""
import json,runpy
from pathlib import Path
from copy import deepcopy
import argparse
p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);args=p.parse_args()
root=Path(__file__).resolve().parents[4]
plan=runpy.run_path(str(root/'app/scraping/uk/aston/first30.py'))['plan']
data=json.loads(Path(args.evidence).read_text(encoding='utf-8-sig'))
manifest=json.loads((root/'data/universities/uk/aston/reviewed_first30.json').read_text())
r=plan(data,manifest)
assert r['summary']==dict(fee_proposals=30,blocked=0,undergraduate_first_year_fees=15,postgraduate_annual_fees=15),r['blocked']
for field,value in [('source_sha256','changed'),('academic_year','2030/31'),('published_duration','99 Years')]:
    altered=deepcopy(manifest);altered[0][field]=value
    assert plan(data,altered)['blocked'],field
print('PASS: 30 proposals; wrong hash, fee year and duration blocked. No database records changed.')

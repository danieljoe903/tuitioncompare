"""Offline preservation/message checks; no database access."""
from pathlib import Path
import json,runpy
root=Path(__file__).resolve().parents[4]
merge=runpy.run_path(str(root/'app/scraping/uk/aston/handoff.py'))['merge_register']
followups=json.loads((root/'data/universities/uk/aston/review_followups.json').read_text(encoding='utf-8'))
prior='\n'.join(uid+' existing narrative must stay unchanged' for uid in ['UK001','UK002','UK003','UK004','UK005','UK006'])
r=dict(generated_at='2026-10-26',summary={'verified_international_fees':48,'pending_international_fees':7},followups=followups,audit_path='audit.json',next_university_id='UK008')
merged=merge(prior,r)
assert merged.startswith(prior)
assert merge(merged,r)==merged
assert len(followups['routes'])==7
for cid,row in followups['routes'].items():
 assert cid in merged and row['message'] in merged
 assert row['confirmation_needed'] and row['source_urls']
assert '2025/26 only' in merged and '2026/27' in merged
for broken in [prior.replace('UK006',''),merged.replace('<!-- university-audit:UK007:final:end -->',''),merged+'\n<!-- university-audit:UK007:final:start -->']:
 try:merge(broken,r)
 except ValueError:pass
 else:raise AssertionError('Partial or malformed register accepted.')
print('PASS: six prior narratives preserved; Aston replacement idempotent; seven reason messages and MBA follow-up retained; malformed/partial register rejected.')
print('Offline checks only. No database records changed.')

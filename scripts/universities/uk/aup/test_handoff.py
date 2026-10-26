"""Offline preservation/idempotency tests. No Django or database writes."""
from pathlib import Path
import runpy
root=Path(__file__).resolve().parents[4]
merge=runpy.run_path(str(root/'app/scraping/uk/aup/handoff.py'))['merge_register']
prior='\n'.join(['Abertay UK001 narrative','Aberystwyth UK002 narrative','Anglia Ruskin UK003 narrative','Arden UK004 narrative','Arts University Bournemouth UK005 narrative'])
report=dict(generated_at='2026-10-25',summary={'verified_international_fees':23},audit_path='audit.json',next_university_id='UK007')
result=merge(prior,report)
assert result.startswith(prior)
assert merge(result,report)==result
for name in ['Graphic Design Top-up','MA Drawing','25 February 2026','September 2027','Has ended']:
    assert name in result
for broken in [prior.replace('UK005',''),result.replace('<!-- university-audit:UK006:final:end -->','')]:
    try:merge(broken,report)
    except ValueError:pass
    else:raise AssertionError('Malformed/partial register accepted')
print('PASS: previous five narratives preserved; AUP replacement idempotent; follow-up retained; partial/malformed registers rejected.')

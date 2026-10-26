"""Preserve later verified foundation fees on earlier import/reconciliation repeats."""
import ast
from pathlib import Path
from datetime import datetime,timezone
import shutil
import sys
ROOT=Path(__file__).resolve().parents[4]
MARKER='# Preserve verified AUB foundation fee confirmation v1.'


def patch_importer(text):
    if MARKER in text:return text
    anchor='                after_course,amount,defaults=plan_route(row,pages)'
    if text.count(anchor)!=1:raise ValueError('Expected the installed remaining-route importer.')
    insert="""
                # Preserve verified AUB foundation fee confirmation v1.
                if owned and isinstance(source.raw_fees.get('foundation_fee_confirmation'),dict) and source.raw_fees['foundation_fee_confirmation'].get('importer')=='aub_foundation_fees_v1':
                    if cid not in FOUNDATION_IDS or source.academic_year!='2027/28' or source.raw_fees.get('annual_tuition')!='19950.00' or source.raw_fees.get('verified') is not True:
                        raise CommandError(cid+': foundation confirmation requires review.')
                    amount='19950.00';defaults=deepcopy(source_state(source))
"""
    text=text.replace(anchor,anchor+insert,1);ast.parse(text);return text


def patch_awards(text):
    old='Provisional tuition remains unverified as final; no deduction.';new='Tuition verification is independent; no Year 0 deduction is established.'
    if new in text:return text
    if text.count(old)!=1:raise ValueError('Expected installed online/foundation scholarship review.')
    text=text.replace(old,new,1);ast.parse(text);return text


def run(apply=False):
    paths=[ROOT/'app/scraping/uk/aub/commands/import_aub_remaining_routes.py',ROOT/'app/scraping/uk/aub/commands/reconcile_aub_online_foundation_awards.py'];plans=[]
    for p,fn in zip(paths,[patch_importer,patch_awards]):
        old=p.read_text(encoding='utf-8-sig');plans.append((p,old,fn(old)))
    print('Files to update:',sum(a!=b for _,a,b in plans))
    if apply:
        backup=ROOT/'script_backups'/('aub-foundation-fees-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True)
        for p,a,b in plans:
            if a!=b:shutil.copy2(p,backup/p.name);p.write_text(b,encoding='utf-8')
        print('Applied. Backup:',backup)
    else:print('Preview only. Use --apply to connect. No database changes.')
if __name__=='__main__':run('--apply' in sys.argv)

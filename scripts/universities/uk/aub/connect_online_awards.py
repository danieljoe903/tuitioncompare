"""Preserve new review metadata on repeated imports and correct comparison labels."""
from pathlib import Path
from datetime import datetime,timezone
import ast
import shutil
import sys
ROOT=Path(__file__).resolve().parents[4]
MARKER='# AUB online progression labels v1'


def patch_view(text):
    if MARKER in text:return text
    old='scholarship_info = "Online scholarship applicability remains pending; no deduction applied."'
    if text.count(old)!=1:raise ValueError('Expected the installed AUB v2 online label; review your comparison view.')
    text=text.replace(old,'# AUB online progression labels v1\n                scholarship_info = ("AUB online progression bursary deducted for the confirmed saved eligibility." if discount > 0 else "Online progression bursary available; saved eligibility and award confirmation required.")',1)
    ast.parse(text);return text


def patch_importer(text):
    marker='# Preserve separately reconciled AUB online/foundation review.'
    if marker in text:return text
    anchor="                before={'course':course_state(c),'tuition':tuition_state(t),'source':source_state(source)};after_t=tuition_state(t);after_t.update(tuition_fee=amount,scholarship='0.00',scholarship_source_id=None)"
    if text.count(anchor)!=1:raise ValueError('Remaining-route importer layout changed; no files written.')
    insert="""                # Preserve separately reconciled AUB online/foundation review.
                if owned and isinstance(source.raw_fees.get('online_foundation_awards'),dict) and source.raw_fees['online_foundation_awards'].get('importer')=='aub_online_progression_v1':
                    defaults['raw_fees']['online_foundation_awards']=deepcopy(source.raw_fees['online_foundation_awards'])
                    defaults['raw_fees']['scholarship_review']=deepcopy(source.raw_fees['scholarship_review'])
"""
    text=text.replace(anchor,insert+anchor,1);ast.parse(text);return text


def run(apply=False):
    views=[]
    for p in (ROOT/'app').rglob('*.py'):
        s=p.read_text(encoding='utf-8-sig')
        if 'def compare_tuition(' in s and any(isinstance(n,ast.FunctionDef) and n.name=='compare_tuition' for n in ast.parse(s).body):views.append((p,s))
    if len(views)!=1:raise ValueError('Cannot isolate the comparison function.')
    ip=ROOT/'app/scraping/uk/aub/commands/import_aub_remaining_routes.py';old=ip.read_text(encoding='utf-8-sig')
    changes=[(views[0][0],views[0][1],patch_view(views[0][1])),(ip,old,patch_importer(old))]
    print('Files to update:',sum(a!=b for _,a,b in changes))
    if apply:
        backup=ROOT/'script_backups'/('aub-online-awards-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True)
        for p,a,b in changes:
            if a!=b:shutil.copy2(p,backup/p.name);p.write_text(b,encoding='utf-8')
        print('Applied. Backup:',backup)
    else:print('Preview only. Use --apply to connect. No database changes.')
if __name__=='__main__':run('--apply' in sys.argv)

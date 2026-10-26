"""Connect the next AUB routes, preserving the existing comparison function."""
import ast
from datetime import datetime,timezone
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[4]
MARKER='# AUB remaining route comparison v2'


def patch(source):
    tree=ast.parse(source);nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compare_tuition']
    if len(nodes)!=1:raise ValueError('Expected one top-level compare_tuition().')
    n=nodes[0];lines=source.splitlines(keepends=True);old=''.join(lines[n.lineno-1:n.end_lineno]);new=old
    if MARKER in old:return source
    def replace(a,b):
        nonlocal new
        if new.count(a)!=1:raise ValueError('Comparison layout changed: '+a[:100])
        new=new.replace(a,b,1)
    replace('    # AUB reviewed campus comparison v1','    '+MARKER+'\n    # AUB reviewed campus comparison v1')
    replace('from app.scraping.uk.aub.calculation import (fee_error as aub_fee_error, fee_metadata as aub_metadata, saved_profile as aub_profile, calculate as aub_calculate, deposit as aub_deposit)', 'from app.scraping.uk.aub.remaining_calculation import (fee_error as aub_fee_error, fee_metadata as aub_metadata, saved_profile as aub_profile, calculate as aub_calculate, deposit as aub_deposit, comparison_filter as aub_filter, review_summary as aub_review_summary)')
    replace('return aub_fee_error(tuition, source)','return aub_fee_error(tuition, source, comparison_basis=comparison_basis)')
    replace('            arden and arden_filter(raw, comparison_basis)','            (arden and arden_filter(raw, comparison_basis)) or (aub and aub_filter(raw, comparison_basis))')
    replace('        # Programme totals are explicitly requested and never ranked with annual fees.','        if aub and source is not None:\n            base.update(aub_review_summary(source, raw))\n\n        # Programme totals are explicitly requested and never ranked with annual fees.')
    replace('        if aub:\n            eligibility_questions = {"scholarship": [], "deposit": []}', '        if aub:\n            eligibility_questions = {"scholarship": [], "deposit": []}\n            if course.course_id in {"C_UK005_23", "C_UK005_40", "C_UK005_41", "C_UK005_42"}:\n                scholarship_info = "Online scholarship applicability remains pending; no deduction applied."')
    result=''.join(lines[:n.lineno-1])+new+''.join(lines[n.end_lineno:]);ast.parse(result);return result


def run(apply=False):
    matches=[]
    for p in (ROOT/'app').rglob('*.py'):
        text=p.read_text(encoding='utf-8-sig')
        if 'def compare_tuition(' in text and any(isinstance(n,ast.FunctionDef) and n.name=='compare_tuition' for n in ast.parse(text).body):matches.append((p,text))
    if len(matches)!=1:raise ValueError('Cannot isolate the comparison view.')
    p,old=matches[0];new=patch(old);print('AUB remaining-route comparison:', 'update' if new!=old else 'unchanged')
    if apply and new!=old:
        backup=ROOT/'script_backups'/('aub-remaining13-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.mkdir(parents=True);shutil.copy2(p,backup/p.name);p.write_text(new,encoding='utf-8');print('Applied. Backup:',backup)
    elif not apply:print('Preview only. Use --apply to connect. No database changes.')
if __name__=='__main__':run('--apply' in sys.argv)

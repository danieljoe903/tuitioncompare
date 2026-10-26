"""Patch only compare_tuition(), preserving other view functions and adapters."""
import ast
from datetime import datetime,timezone
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[4]
MARKER='# AUB reviewed campus comparison v1'


def patch(source):
    tree=ast.parse(source)
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='compare_tuition']
    if len(nodes)!=1:raise ValueError('Expected one top-level compare_tuition function.')
    node=nodes[0];lines=source.splitlines(keepends=True);old=''.join(lines[node.lineno-1:node.end_lineno])
    if MARKER in old:return source
    new=old
    def change(a,b):
        nonlocal new
        if new.count(a)!=1:raise ValueError('Comparison layout changed; review anchor: '+a[:90])
        new=new.replace(a,b,1)
    change('    # Arden level fee integration v3',
           '    '+MARKER+'\n    from app.scraping.uk.aub.calculation import (fee_error as aub_fee_error, fee_metadata as aub_metadata, saved_profile as aub_profile, calculate as aub_calculate, deposit as aub_deposit)\n\n    # Arden level fee integration v3')
    change('        arden = country == "UK" and university_id == "UK004"',
           '        arden = country == "UK" and university_id == "UK004"\n        aub = country == "UK" and university_id == "UK005"')
    change('        if source is None:\n            return (',
           '        if str(tuition.course.university_id) == "UK005":\n            return aub_fee_error(tuition, source)\n        if source is None:\n            return (')
    change('        if aberystwyth or aru or arden:', '        if aberystwyth or aru or arden or aub:')
    change('        metadata = {} if arden else dict(fee_metadata(tuition) or {})',
           '        metadata = {} if (arden or aub) else dict(fee_metadata(tuition) or {})')
    change('            metadata.update(arden_fee_metadata(request, source, raw))',
           '            metadata.update(arden_fee_metadata(request, source, raw))\n        if aub:\n            metadata.update(aub_metadata(source, raw))')
    change('        course_profile = dict(profile)',
           '        course_profile = dict(profile)\n        if aub:\n            course_profile.update(aub_profile(request, course.course_id, academic_year))')
    change('        calculation = calculate_scholarships(',
           '        calculation = (aub_calculate if aub else calculate_scholarships)(')
    change('university_id not in {"UK001", "UK002", "UK003", "UK004"}',
           'university_id not in {"UK001", "UK002", "UK003", "UK004", "UK005"}')
    change('        if arden and metadata["comparison_period"] == "total_programme":',
           '        if aub and discount == 0:\n            scholarship_info = "Scholarship eligibility or award amount not confirmed. Complete the scholarship page."\n\n        if arden and metadata["comparison_period"] == "total_programme":')
    change('        elif aru:\n            deposit_calculation',
           '        elif aub:\n            deposit_calculation = aub_deposit(tuition=tuition, academic_year=academic_year)\n\n        elif aru:\n            deposit_calculation')
    change('        verified_fee = abertay or aberystwyth or aru or arden',
           '        if aub:\n            eligibility_questions = {"scholarship": [], "deposit": []}\n\n        verified_fee = abertay or aberystwyth or aru or arden or aub')
    result=''.join(lines[:node.lineno-1])+new+''.join(lines[node.end_lineno:]);ast.parse(result);return result


def run(apply=False):
    candidates=[]
    for p in (ROOT/'app').rglob('*.py'):
        if '__pycache__' in p.parts:continue
        text=p.read_text(encoding='utf-8-sig')
        if 'def compare_tuition(' not in text:continue
        tree=ast.parse(text)
        if any(isinstance(n,ast.FunctionDef) and n.name=='compare_tuition' for n in tree.body):candidates.append((p,text))
    if len(candidates)!=1:raise ValueError('Cannot isolate one comparison view; found '+str(len(candidates)))
    path,old=candidates[0];new=patch(old)
    print('Comparison integration:', 'unchanged' if new==old else 'update')
    if apply and new!=old:
        backup=ROOT/'script_backups'/('aub-comparison-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.mkdir(parents=True,exist_ok=False);shutil.copy2(path,backup/path.name)
        path.write_text(new,encoding='utf-8');print('Applied. Backup:',backup)
    elif not apply:print('Preview only. Use --apply to connect; no database writes.')

if __name__=='__main__':run(apply='--apply' in sys.argv)

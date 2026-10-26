"""Connect AUP while preserving the five previous university adapters."""
import ast
from datetime import datetime,timezone
from pathlib import Path
import shutil,sys
ROOT=Path(__file__).resolve().parents[4]
MARKER='# AUP reviewed23 comparison v1'

def patch(source):
    tree=ast.parse(source);nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compare_tuition']
    if len(nodes)!=1:raise ValueError('Expected one top-level compare_tuition().')
    n=nodes[0];lines=source.splitlines(keepends=True);old=''.join(lines[n.lineno-1:n.end_lineno]);new=old
    if MARKER in old:return source
    def replace(a,b):
        nonlocal new
        if new.count(a)!=1:raise ValueError('Comparison layout changed; expected one block: '+a[:110])
        new=new.replace(a,b,1)
    replace('    # AUB remaining route comparison v2','    '+MARKER+'\n    from app.scraping.uk.aup.calculation import (fee_error as aup_fee_error, fee_metadata as aup_metadata, saved_profile as aup_profile, calculate as aup_calculate, deposit as aup_deposit)\n\n    # AUB remaining route comparison v2')
    replace('        aub = country == "UK" and university_id == "UK005"','        aub = country == "UK" and university_id == "UK005"\n        aup = country == "UK" and university_id == "UK006"')
    replace('        if str(tuition.course.university_id) == "UK005":','        if str(tuition.course.university_id) == "UK006":\n            return aup_fee_error(tuition, source)\n        if str(tuition.course.university_id) == "UK005":')
    replace('        if aberystwyth or aru or arden or aub:','        if aberystwyth or aru or arden or aub or aup:')
    replace('        metadata = {} if (arden or aub) else dict(fee_metadata(tuition) or {})','        metadata = {} if (arden or aub or aup) else dict(fee_metadata(tuition) or {})')
    replace('            metadata.update(aub_metadata(source, raw))','            metadata.update(aub_metadata(source, raw))\n        if aup:\n            metadata.update(aup_metadata(source, raw))')
    replace('        course_profile = dict(profile)','        course_profile = dict(profile)\n        if aup:\n            course_profile.update(aup_profile(request, course.course_id, academic_year))')
    replace('        calculation = (aub_calculate if aub else calculate_scholarships)(',
        '        if aup:\n            comparison_tuition.source_metadata = source\n        calculation = (aup_calculate if aup else aub_calculate if aub else calculate_scholarships)(')
    replace('university_id not in {"UK001", "UK002", "UK003", "UK004", "UK005"}',
        'university_id not in {"UK001", "UK002", "UK003", "UK004", "UK005", "UK006"}')
    replace('        elif aub:\n            deposit_calculation',
        '        elif aup:\n            deposit_calculation = aup_deposit(tuition=tuition, academic_year=academic_year, profile=course_profile)\n\n        elif aub:\n            deposit_calculation')
    replace('        verified_fee = abertay or aberystwyth or aru or arden or aub',
        '        if aup:\n            eligibility_questions = {"scholarship": [], "deposit": []}\n            scholarship_info = "Uses the confirmed Alumni deduction for this intake." if discount > 0 else "Scholarship eligibility or cohort applicability requires confirmation. Visit the scholarship page."\n\n        verified_fee = abertay or aberystwyth or aru or arden or aub or aup')
    result=''.join(lines[:n.lineno-1])+new+''.join(lines[n.end_lineno:]);ast.parse(result);return result

def run(apply=False):
    matches=[]
    for p in (ROOT/'app').rglob('*.py'):
        if '__pycache__' in p.parts:continue
        text=p.read_text(encoding='utf-8-sig')
        if 'def compare_tuition(' in text and any(isinstance(n,ast.FunctionDef) and n.name=='compare_tuition' for n in ast.parse(text).body):matches.append((p,text))
    if len(matches)!=1:raise ValueError('Cannot isolate one installed comparison view.')
    path,old=matches[0];new=patch(old);print('AUP comparison integration:', 'unchanged' if new==old else 'update')
    if apply and new!=old:
        backup=ROOT/'script_backups'/('aup-reviewed23-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))/path.relative_to(ROOT)
        backup.parent.mkdir(parents=True);shutil.copy2(path,backup);path.write_text(new,encoding='utf-8');print('Applied. Backup:',backup)
    elif not apply:print('No files changed. Use --apply to connect.')
if __name__=='__main__':run('--apply' in sys.argv)

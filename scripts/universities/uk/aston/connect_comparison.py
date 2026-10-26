"""Connect Aston without changing previously reviewed university adapters."""
import ast,shutil,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[4]
MARKER='# Aston first30 comparison v1'

def patch(source):
    tree=ast.parse(source);nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compare_tuition']
    if len(nodes)!=1:raise ValueError('Expected one top-level compare_tuition function.')
    n=nodes[0];lines=source.splitlines(keepends=True);old=''.join(lines[n.lineno-1:n.end_lineno]);new=old
    if MARKER in old:return source
    def replace(a,b):
        nonlocal new
        if new.count(a)!=1:raise ValueError('Comparison layout changed; expected one block: '+a[:100])
        new=new.replace(a,b,1)
    replace('    # AUP reviewed23 comparison v1','    '+MARKER+'\n    from app.scraping.uk.aston.calculation import (fee_error as aston_fee_error, fee_metadata as aston_metadata, calculate as aston_calculate, deposit as aston_deposit)\n    # AUP reviewed23 comparison v1')
    replace('        aup = country == "UK" and university_id == "UK006"','        aup = country == "UK" and university_id == "UK006"\n        aston = country == "UK" and university_id == "UK007"')
    replace('        if str(tuition.course.university_id) == "UK006":','        if str(tuition.course.university_id) == "UK007":\n            return aston_fee_error(tuition, source)\n        if str(tuition.course.university_id) == "UK006":')
    replace('        if aberystwyth or aru or arden or aub or aup:','        if aberystwyth or aru or arden or aub or aup or aston:')
    replace('metadata = {} if (arden or aub or aup) else dict(fee_metadata(tuition) or {})','metadata = {} if (arden or aub or aup or aston) else dict(fee_metadata(tuition) or {})')
    replace('            metadata.update(aup_metadata(source, raw))','            metadata.update(aup_metadata(source, raw))\n        if aston:\n            metadata.update(aston_metadata(source, raw))')
    replace('        calculation = (aup_calculate if aup else aub_calculate if aub else calculate_scholarships)(',
        '        calculation = (aston_calculate if aston else aup_calculate if aup else aub_calculate if aub else calculate_scholarships)(')
    replace('university_id not in {"UK001", "UK002", "UK003", "UK004", "UK005", "UK006"}',
        'university_id not in {"UK001", "UK002", "UK003", "UK004", "UK005", "UK006", "UK007"}')
    replace('        elif aup:\n            deposit_calculation',
        '        elif aston:\n            deposit_calculation = aston_deposit(tuition=tuition, academic_year=academic_year, profile=course_profile)\n\n        elif aup:\n            deposit_calculation')
    replace('        verified_fee = abertay or aberystwyth or aru or arden or aub or aup',
        '        if aston:\n            eligibility_questions = {"scholarship": [], "deposit": []}\n            scholarship_info = "Aston scholarship applicability requires exact-cohort review. Visit the scholarship page."\n\n        verified_fee = abertay or aberystwyth or aru or arden or aub or aup or aston')
    result=''.join(lines[:n.lineno-1])+new+''.join(lines[n.end_lineno:]);ast.parse(result);return result

def run(apply=False):
    matches=[]
    for p in (ROOT/'app').rglob('*.py'):
        text=p.read_text(encoding='utf-8-sig')
        if 'def compare_tuition(' in text and any(isinstance(n,ast.FunctionDef) and n.name=='compare_tuition' for n in ast.parse(text).body):matches.append((p,text))
    if len(matches)!=1:raise ValueError('Cannot isolate one installed comparison view.')
    path,old=matches[0];new=patch(old)
    print('Aston comparison integration:', 'unchanged' if new==old else 'update')
    if apply and new!=old:
        backup=ROOT/'script_backups'/('aston-first30-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))/path.relative_to(ROOT)
        backup.parent.mkdir(parents=True);shutil.copy2(path,backup);path.write_text(new,encoding='utf-8');print('Applied. Backup:',backup)
    elif not apply:print('No files changed. Use --apply to connect.')
if __name__=='__main__':run('--apply' in sys.argv)

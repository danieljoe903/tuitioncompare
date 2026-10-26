"""Preview/apply the reviewed Arden integration in app/views.py; no DB writes."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'manage.py').is_file())
MARKER = '# Arden campus PG integration v1'


def patch(source):
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'compare_tuition')
    lines = source.splitlines(keepends=True)
    original = ''.join(lines[fn.lineno - 1:fn.end_lineno])
    if MARKER in original:
        return source, False
    updated = original

    def change(old, new):
        nonlocal updated
        if updated.count(old) != 1:
            raise ValueError('Unexpected comparison code; no edit applied. Anchor: ' + old[:100])
        updated = updated.replace(old, new, 1)

    change('    residence = request.GET.get',
           '    ' + MARKER + '\n'
           '    from app.scraping.uk.arden.calculation import (\n'
           '        build_profile as arden_profile, selection_error as arden_selection_error,\n'
           '        calculate_deposit as arden_deposit, eligibility_questions as arden_questions,\n'
           '    )\n\n    residence = request.GET.get')
    change('        aru = country == "UK" and university_id == "UK003"',
           '        aru = country == "UK" and university_id == "UK003"\n'
           '        arden = country == "UK" and university_id == "UK004"')
    change('        if aberystwyth or aru:', '        if aberystwyth or aru or arden:')
    change('        try:\n            amount = Decimal(str(tuition.tuition_fee))',
           '        if arden:\n'
           '            reason = arden_selection_error(request, raw)\n'
           '            if reason:\n'
           '                results.append(unavailable(reason))\n'
           '                continue\n\n'
           '        try:\n            amount = Decimal(str(tuition.tuition_fee))')
    change('        course_profile = dict(profile)',
           '        course_profile = dict(profile)\n'
           '        if arden:\n'
           '            course_profile.update(arden_profile(request, raw))\n'
           '            eligibility_questions["scholarship"] = merge_questions(\n'
           '                eligibility_questions["scholarship"], arden_questions(),\n'
           '            )')
    change('university_id not in {"UK001", "UK002", "UK003"}',
           'university_id not in {"UK001", "UK002", "UK003", "UK004"}')
    change('        elif aru:\n            deposit_calculation = calculate_aru_deposit(',
           '        elif arden:\n'
           '            deposit_calculation = arden_deposit(\n'
           '                tuition=tuition, academic_year=academic_year,\n'
           '            )\n\n'
           '        elif aru:\n            deposit_calculation = calculate_aru_deposit(')
    change('        verified_fee = abertay or aberystwyth or aru',
           '        verified_fee = abertay or aberystwyth or aru or arden')
    change('            "scholarship_calculation": calculation,',
           '            **({"arden_selection": {\n'
           '                "campus": course_profile.get("arden_campus"),\n'
           '                "intake": course_profile.get("arden_intake"),\n'
           '            }} if arden else {}),\n'
           '            "scholarship_calculation": calculation,')
    result = ''.join(lines[:fn.lineno - 1]) + updated + ''.join(lines[fn.end_lineno:])
    compile(result, 'app/views.py', 'exec')
    return result, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = ROOT / 'app/views.py'
    before = target.read_bytes()
    source = before.decode('utf-8-sig').replace('\r\n', '\n')
    updated, changed = patch(source)
    print('Comparison integration:', 'update' if changed else 'unchanged')
    if not changed or not args.apply:
        print('No files changed.' if not args.apply else 'Already connected.')
        return
    if not (ROOT / 'app/scraping/uk/arden/calculation.py').is_file():
        raise ValueError('Install the supplied Arden calculation.py first.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = ROOT / 'script_backups' / ('arden-integration-' + stamp)
    backup.mkdir(parents=True)
    (backup / 'views.py').write_bytes(before)
    after = updated.encode('utf-8')
    (backup / 'manifest.json').write_text(json.dumps({
        'target': 'app/views.py', 'before_sha256': hashlib.sha256(before).hexdigest(),
        'after_sha256': hashlib.sha256(after).hexdigest(),
    }, indent=2), encoding='utf-8')
    if target.read_bytes() != before:
        raise ValueError('views.py changed during planning; no edit applied.')
    temporary = target.with_name('views.py.arden-tmp')
    try:
        temporary.write_bytes(after)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    print('Comparison connected. Backup:', backup)
    print('No database records changed.')


if __name__ == '__main__':
    main()

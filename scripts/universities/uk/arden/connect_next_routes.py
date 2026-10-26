"""Preview/apply explicit Arden programme-total comparison support. No DB writes."""
import argparse
import ast
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'manage.py').is_file())
MARKER = '# Arden route basis integration v2'


def patch(source):
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'compare_tuition')
    lines = source.splitlines(keepends=True)
    before = ''.join(lines[fn.lineno - 1:fn.end_lineno])
    if MARKER in before:
        return source, False
    if '# Arden campus PG integration v1' not in before:
        raise ValueError('The tested first-batch Arden connector must be installed first.')
    updated = before

    def change(old, new):
        nonlocal updated
        if updated.count(old) != 1:
            raise ValueError('Unexpected comparison code; no edit applied. Anchor: ' + old[:100])
        updated = updated.replace(old, new, 1)

    change('    # Arden campus PG integration v1',
           '    ' + MARKER + '\n    # Arden campus PG integration v1')
    change('from app.scraping.uk.arden.calculation import (',
           'from app.scraping.uk.arden.route_calculation import (')
    change('    residence = request.GET.get',
           '    comparison_basis = request.GET.get("comparison_basis", "annual").strip()\n'
           '    if comparison_basis not in {"annual", "programme_total"}:\n'
           '        return JsonResponse({"error": "Invalid comparison_basis."}, status=400)\n\n'
           '    residence = request.GET.get')
    change('        if source.fee_basis not in {\n            "annual_tuition",\n            "first_year_tuition",\n            "full_time_course_fee",\n        }:',
           '        if source.fee_basis not in {\n            "annual_tuition",\n            "first_year_tuition",\n            "full_time_course_fee",\n        } and not (\n'
           '            str(tuition.course.university_id) == "UK004"\n'
           '            and source.fee_basis == "total_programme_fee"\n'
           '            and raw.get("importer") == "arden_online_pg_v2"\n'
           '        ):')
    change('            published = Decimal(str(raw["annual_tuition"]))',
           '            published = Decimal(str(\n'
           '                raw["total_programme_fee"]\n'
           '                if str(tuition.course.university_id) == "UK004"\n'
           '                and source.fee_basis == "total_programme_fee"\n'
           '                else raw["annual_tuition"]\n'
           '            ))')
    change('        # Preserve pending-route questions even when the fee is unavailable.',
           '        # Programme totals are explicitly requested and never ranked with annual fees.\n'
           '        if comparison_basis == "programme_total" and not (\n'
           '            arden and source is not None\n'
           '            and source.fee_basis == "total_programme_fee"\n'
           '        ):\n'
           '            continue\n\n'
           '        # Preserve pending-route questions even when the fee is unavailable.')
    change('        metadata = dict(fee_metadata(tuition) or {})',
           '        metadata = {} if arden else dict(fee_metadata(tuition) or {})')
    change('        award_level = {',
           '        if arden:\n'
           '            is_programme_total = source.fee_basis == "total_programme_fee"\n'
           '            metadata.update(\n'
           '                fee_basis=source.fee_basis,\n'
           '                fee_label="Total programme fee" if is_programme_total else "Annual tuition fee",\n'
           '                comparison_period="total_programme" if is_programme_total else "annual",\n'
           '                fee_note=raw.get("fee_note", ""),\n'
           '            )\n\n'
           '        award_level = {')
    change('eligibility_questions["scholarship"], arden_questions(),',
           'eligibility_questions["scholarship"], arden_questions(request, raw),')
    change('        discount = Decimal(str(calculation["discount"]))',
           '        if arden:\n'
           '            calculation["comparison_period"] = metadata["comparison_period"]\n\n'
           '        discount = Decimal(str(calculation["discount"]))')
    change('        deposit_calculation = {',
           '        if arden and source.fee_basis == "total_programme_fee":\n'
           '            scholarship_info = scholarship_info.replace(\n'
           '                "(for the compared study year)", "(for the full programme)"\n'
           '            )\n\n'
           '        deposit_calculation = {')
    change('            "residency": "INTL",\n        },',
           '            "residency": "INTL",\n'
           '            "comparison_basis": comparison_basis,\n        },')
    result = ''.join(lines[:fn.lineno - 1]) + updated + ''.join(lines[fn.end_lineno:])
    compile(result, 'app/views.py', 'exec')
    return result, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = ROOT / 'app/views.py'
    before = target.read_bytes()
    updated, changed = patch(before.decode('utf-8-sig').replace('\r\n', '\n'))
    plans = [(target, before, updated.encode('utf-8'))] if changed else []
    # Preserve the extended deposit scope when the original PG importer is rerun.
    first_importer = ROOT / 'scripts/universities/uk/arden/import_campus_postgraduate.py'
    original = first_importer.read_bytes()
    text = original.decode('utf-8-sig').replace('\r\n', '\n')
    scope_marker = '# Preserve reviewed deposit scopes added by later Arden batches.'
    if scope_marker not in text:
        anchor = "        policy_rules['importer'] = IMPORTER"
        if text.count(anchor) != 1:
            raise ValueError('Unexpected first-batch importer; deposit scope patch needs review.')
        replacement = (anchor + '\n        ' + scope_marker + '\n'
            '        if policy is not None:\n'
            '            policy_rules["course_ids"] = sorted(\n'
            '                set(policy_rules["course_ids"]) | set(policy.rules.get("course_ids", []))\n'
            '            )')
        text = text.replace(anchor, replacement, 1)
        compile(text, str(first_importer), 'exec')
        plans.append((first_importer, original, text.encode('utf-8')))
    print('Arden route/basis integration:', 'update' if changed else 'unchanged')
    print('Files to update:', len(plans))
    if not args.apply or not plans:
        print('No files changed.')
        return
    if not (ROOT / 'app/scraping/uk/arden/route_calculation.py').is_file():
        raise ValueError('Install route_calculation.py first.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = ROOT / 'script_backups' / ('arden-routes-v2-' + stamp)
    backup.mkdir(parents=True)
    manifest = []
    for file, old, new in plans:
        relative = file.relative_to(ROOT)
        saved = backup / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(old)
        manifest.append(dict(target=str(relative), before_sha256=hashlib.sha256(old).hexdigest(), after_sha256=hashlib.sha256(new).hexdigest()))
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    applied = []
    try:
        for file, old, new in plans:
            if file.read_bytes() != old:
                raise ValueError('File changed during planning: ' + str(file))
            temp = file.with_name(file.name + '.arden-tmp')
            try:
                temp.write_bytes(new)
                temp.replace(file)
                applied.append((file, old))
            finally:
                if temp.exists():
                    temp.unlink()
    except BaseException:
        for file, old in reversed(applied):
            file.write_bytes(old)
        raise
    print('Applied. Backup:', backup)
    print('No database records changed.')


if __name__ == '__main__':
    main()

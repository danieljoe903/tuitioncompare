"""Install Arden level-fee comparison support; preview by default, --apply saves."""
import argparse
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

MARKER = '# Arden level fee integration v3'


def patch(source):
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'compare_tuition')
    lines = source.splitlines(keepends=True)
    before = ''.join(lines[fn.lineno - 1:fn.end_lineno])
    if MARKER in before:
        return source, False
    if '# Arden route basis integration v2' not in before:
        raise ValueError('Install the tested previous Arden integration before this connector.')
    updated = before

    def change(old, new):
        nonlocal updated
        if updated.count(old) != 1:
            raise ValueError('Comparison code differs; no file changed. Anchor: ' + old[:100])
        updated = updated.replace(old, new, 1)

    change('    # Arden route basis integration v2', '    ' + MARKER + '\n    # Arden route basis integration v2')
    change('from app.scraping.uk.arden.route_calculation import (', 'from app.scraping.uk.arden.study_calculation import (\n        comparison_amount as arden_amount, fee_metadata as arden_fee_metadata,\n        verification_error as arden_level_fee_error, filter_matches as arden_filter,')
    change('if comparison_basis not in {"annual", "programme_total"}:', 'if comparison_basis not in {"annual", "programme_total", "per_level"}:')
    change('        if source.fee_basis not in {',
           '        if (str(tuition.course.university_id) == "UK004"\n'
           '                and raw.get("importer") == "arden_online_ug_levels_v3"):\n'
           '            return arden_level_fee_error(tuition, source)\n\n'
           '        if source.fee_basis not in {')
    change('and raw.get("importer") == "arden_online_pg_v2"',
           'and raw.get("importer") in {"arden_online_pg_v2", "arden_online_ug_topup_v3"}')
    change('        if comparison_basis == "programme_total" and not (\n            arden and source is not None\n            and source.fee_basis == "total_programme_fee"\n        ):',
           '        if comparison_basis != "annual" and not (\n            arden and arden_filter(raw, comparison_basis)\n        ):')
    change('        try:\n            amount = Decimal(str(tuition.tuition_fee))',
           '        try:\n            amount = arden_amount(request, raw, tuition) if arden else Decimal(str(tuition.tuition_fee))')
    change('        if arden:\n            is_programme_total = source.fee_basis == "total_programme_fee"\n            metadata.update(\n                fee_basis=source.fee_basis,\n                fee_label="Total programme fee" if is_programme_total else "Annual tuition fee",\n                comparison_period="total_programme" if is_programme_total else "annual",\n                fee_note=raw.get("fee_note", ""),\n            )',
           '        if arden:\n            metadata.update(arden_fee_metadata(request, source, raw))')
    change('        if arden and source.fee_basis == "total_programme_fee":',
           '        if arden and metadata["comparison_period"] == "total_programme":')
    change('            "postgraduate_taught",\n        }',
           '            "postgraduate_taught",\n            "arden_online_gross_quote",\n            "arden_comparison_period",\n            "arden_online_tier2_country_confirmed",\n        }')
    change('            calculation["comparison_period"] = metadata["comparison_period"]',
           '            calculation["comparison_period"] = metadata["comparison_period"]\n'
           '            if metadata["comparison_period"] == "per_level":\n'
           '                calculation.setdefault("notes", []).append(\n'
           '                    "Programme-wide awards require programme_total; no unverified per-level allocation is deducted."\n'
           '                )')
    result = ''.join(lines[:fn.lineno - 1]) + updated + ''.join(lines[fn.end_lineno:])
    compile(result, 'app/views.py', 'exec')
    return result, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = next(p for p in Path(__file__).resolve().parents if (p / 'manage.py').is_file())
    target = root / 'app/views.py'
    old = target.read_bytes()
    updated, changed = patch(old.decode('utf-8-sig').replace('\r\n', '\n'))
    print('Arden level-fee integration:', 'update' if changed else 'unchanged')
    if not args.apply or not changed:
        print('No files changed.')
        return
    if not (root / 'app/scraping/uk/arden/study_calculation.py').is_file():
        raise ValueError('Install study_calculation.py first.')
    backup = root / 'script_backups' / ('arden-levels-v3-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True)
    (backup / 'views.py').write_bytes(old)
    (backup / 'manifest.json').write_text(json.dumps({'target': 'app/views.py', 'before_sha256': hashlib.sha256(old).hexdigest(), 'after_sha256': hashlib.sha256(updated.encode()).hexdigest()}, indent=2), encoding='utf-8')
    if target.read_bytes() != old:
        raise ValueError('views.py changed while planning; no overwrite.')
    temp = target.with_name('views.py.arden-v3-tmp')
    try:
        temp.write_text(updated, encoding='utf-8')
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    print('Applied. Backup:', backup)
    print('No database records changed.')


if __name__ == '__main__':
    main()

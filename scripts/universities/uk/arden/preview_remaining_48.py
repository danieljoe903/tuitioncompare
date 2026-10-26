"""Read-only Arden review. Invoke through Django shell as documented."""
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def check_evidence(base, manifest):
    course_path = base / manifest['evidence_course_report']
    fee_path = base / manifest['evidence_fee_report']
    courses = read_json(course_path)
    fees = read_json(fee_path)
    pages = {p['source_url']: p for p in courses['pages']}
    for row in manifest['proposals']:
        if 'source_url' not in row:
            continue
        page = pages[row['source_url']]
        raw = (course_path.parent / Path(page['html_file']).name).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != page['sha256'] or actual != row['source_html_sha256']:
            raise ValueError(f"{row['course_id']}: course HTML hash differs.")
        captures = [r for r in fees['responses'] if r['course_page'] == row['source_url']]
        if len(captures) != len(row['fee_evidence']):
            raise ValueError(f"{row['course_id']}: fee capture count differs.")
        for capture in row['fee_evidence']:
            matches = [r for r in captures if r['country'] == capture['country']]
            if len(matches) != 1:
                raise ValueError(f"{row['course_id']}: country evidence is ambiguous.")
            source = matches[0]
            raw = (fee_path.parent / Path(source['raw_response_file']).name).read_bytes()
            actual = hashlib.sha256(raw).hexdigest()
            if actual != source['response_sha256'] or actual != capture['sha256']:
                raise ValueError(f"{row['course_id']}: fee response hash differs.")
            if source['text'] != capture['text'] or source['fee_source_url'] != capture['source_url']:
                raise ValueError(f"{row['course_id']}: fee report contents differ.")


def run(course_evidence=None, fee_evidence=None):
    from django.conf import settings
    from django.core.serializers.json import DjangoJSONEncoder
    from app.models import Course, Tuition

    base = Path(settings.BASE_DIR)
    manifest = read_json(base / 'data/universities/uk/arden/remaining_48_manifest.json')
    if course_evidence:
        manifest['evidence_course_report'] = str(course_evidence)
    if fee_evidence:
        manifest['evidence_fee_report'] = str(fee_evidence)
    # All source checks finish before the database review. This script has no writes.
    check_evidence(base, manifest)
    proposals = copy.deepcopy(manifest['proposals'])
    ids = [r['course_id'] for r in proposals]
    if len(ids) != 48 or len(set(ids)) != 48:
        raise ValueError('Expected exactly 48 unique course IDs.')
    current = {r['course_id']: r for r in Course.objects.filter(
        university_id='UK004', course_id__in=ids).values()}
    tuitions = list(Tuition.objects.filter(
        course__university_id='UK004', course_id__in=ids, residency='INTL').values())
    blocked = []
    identity_fields = ('title', 'level', 'location', 'duration', 'start_month', 'start_year', 'course_url', 'is_active')
    for proposal in proposals:
        cid = proposal['course_id']
        course = current.get(cid)
        if course is None:
            blocked.append({'course_id': cid, 'reason': 'Course missing from UK004.'})
            continue
        proposal['current_database_course'] = course
        changes = {key: {'collected': proposal['stored_course'].get(key), 'current': course.get(key)}
                   for key in identity_fields if proposal['stored_course'].get(key) != course.get(key)}
        if changes:
            blocked.append({'course_id': cid, 'reason': 'Course changed since evidence collection.', 'differences': changes})
        matches = [t for t in tuitions if t['course_id'] == cid]
        proposal['current_international_tuitions'] = matches
        if len(matches) != 1 or matches[0]['currency'] != 'GBP':
            blocked.append({'course_id': cid, 'reason': 'One GBP international tuition row is required.'})
        print(f"{cid}: {proposal['category']}")
    counts = dict(Counter(p['category'] for p in proposals))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'university_id': 'UK004', 'mode': 'preview',
        'database_changes': False, 'mappings_approved': False,
        'summary': {'reviewed': len(proposals), 'categories': counts, 'blocked_items': len(blocked)},
        'evidence_course_report': manifest['evidence_course_report'],
        'evidence_fee_report': manifest['evidence_fee_report'],
        'proposals': proposals, 'blocked': blocked,
        'note': 'Scholarship amounts are conditional proposals, not saved rules or actual deductions. Per-level fees require comparison support. No apply option is provided by this review script.',
    }
    folder = base / 'logs/arden'
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f'remaining-48-preview-{stamp}.json'
    output.write_text(json.dumps(report, cls=DjangoJSONEncoder, indent=2, ensure_ascii=False), encoding='utf-8')
    print('\nReviewed records: 48')
    for name, count in counts.items():
        print(f'{name}: {count}')
    print(f'Blocked items: {len(blocked)}')
    print(f'Report: {output}')
    print('Preview only. No database records changed.')
    print('No scholarship deductions or deposit amounts were applied by this preview.')
    return output

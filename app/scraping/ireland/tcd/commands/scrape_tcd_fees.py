"""Preview TCD undergraduate fees. This command never writes to the database."""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal
from app.models import University, Course
from app.fee_models import TuitionSource
from app.models import Course, Tuition
from app.fee_models import TuitionSource, TuitionUpdateAudit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pathlib import Path

SOURCE_URL = 'https://www.tcd.ie/courses/undergraduate/fees/'
HEADERS = [
    'Academic Year', 'School', 'Course Name', 'Course Code',
    'Fee Profile Code', 'EU Tuition', 'EU Student Contribution',
    'EU Student Total Fee - Year 1', 'Non-EU Student Total Fee - Year 1',
]


def clean(value):
    return ' '.join(value.split())


def normalize_title(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    return clean(value.replace('\u2019', "'"))


def parse_amount(raw):
    """Accept exact euro amounts only. Keep qualifications as unresolved text."""
    raw = clean(raw)
    pattern = r'€\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{2}))?'
    match = re.fullmatch(pattern, raw)
    if not match:
        return None
    return Decimal(match.group(1).replace(',', '') + '.' + (match.group(2) or '00'))


def parse_fees(html, academic_year):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.select_one('#csvTable')
    if table is None:
        raise ValueError('Fee table #csvTable is missing. Check the source structure.')
    headers = [clean(c.get_text(' ', strip=True)) for c in table.select('thead th')]
    if not headers:
        first = table.find('tr')
        headers = [clean(c.get_text(' ', strip=True)) for c in first.find_all('th')] if first else []
    if headers != HEADERS:
        raise ValueError(f'Unexpected fee columns: {headers!r}')
    records = []
    years = set()
    for row in table.select('tr'):
        cells = row.find_all('td', recursive=False)
        if not cells:
            continue
        if len(cells) != len(HEADERS):
            raise ValueError(f'Unexpected row width: {len(cells)}. Expected {len(HEADERS)}.')
        values = [clean(c.get_text(' ', strip=True)) for c in cells]
        years.add(values[0])
        if values[0] != academic_year:
            continue
        raw_fees = dict(zip(HEADERS[5:], values[5:]))
        amounts = {key: parse_amount(raw) for key, raw in raw_fees.items()}
        notes = [f'{key}: {raw or "blank"}' for key, raw in raw_fees.items() if amounts[key] is None]
        if not values[2] or not values[3]:
            notes.append('Source course name or course code is missing; review manually.')
        tuition = amounts[HEADERS[5]]
        contribution = amounts[HEADERS[6]]
        total = amounts[HEADERS[7]]
        if all(x is not None for x in (tuition, contribution, total)) and tuition + contribution != total:
            notes.append('EU tuition plus contribution does not equal the published EU total.')
        records.append({
            'academic_year': values[0], 'school': values[1],
            'course_name': values[2], 'source_course_code': values[3],
            'fee_profile_code': values[4], 'currency': 'EUR',
            'study_year': 1, 'source_url': SOURCE_URL,
            'raw_fees': raw_fees,
            'amounts': {key: str(amount) if amount is not None else None for key, amount in amounts.items()},
            'review_notes': notes,
        })
    if not records:
        raise ValueError(f'No rows for {academic_year}. Available years: {sorted(years)}')
    return records


def match_courses(records, courses):
    """Only normalized exact titles are candidates, never approved mappings."""
    by_title = defaultdict(list)
    for course in courses:
        by_title[normalize_title(course['title'])].append(course)
    for record in records:
        candidates = by_title.get(normalize_title(record['course_name']), [])
        record['candidate_course_ids'] = [str(c['course_id']) for c in candidates]
        record['match_status'] = (
            'exact_title_candidate' if len(candidates) == 1
            else 'ambiguous_title' if candidates else 'unmatched'
        )
    return records


class Command(BaseCommand):
    help = 'Preview official TCD undergraduate fees and course-match candidates; no database writes.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Save unambiguous exact-title matches and explicitly mapped rows.')
        parser.add_argument('--mapping', help='Optional JSON list with source_course_name, source_course_code and course_id.')
        parser.add_argument('--academic-year', required=True, help='Example: 2026/27')
        parser.add_argument('--university-id', help='Existing TCD university ID, if slug lookup fails.')
        parser.add_argument('--limit', type=int, default=10, help='Preview row limit; 0 prints all rows.')

    def handle(self, *args, **options):
        
        year = options['academic_year']
        if not re.fullmatch(r'\d{4}/\d{2}', year) or int(year[-2:]) != (int(year[:4]) + 1) % 100:
            raise CommandError('Use consecutive academic years, for example 2026/27.')
        if options['limit'] < 0:
            raise CommandError('--limit must be zero or greater.')
        universities = University.objects.filter(
            university_id=options['university_id']
        ) if options['university_id'] else University.objects.filter(slug='trinity-college-dublin')
        if universities.count() != 1:
            raise CommandError('TCD university lookup is missing or ambiguous. Supply --university-id.')
        university = universities.get()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET'])
        with requests.Session() as session:
            session.headers['User-Agent'] = 'TuitionCompareFeePreview/1.0'
            session.mount('https://', HTTPAdapter(max_retries=retries))
            try:
                response = session.get(SOURCE_URL, timeout=(10, 30))
                response.raise_for_status()
                records = parse_fees(response.text, year)
            except (requests.RequestException, ValueError) as exc:
                raise CommandError(str(exc)) from exc
        courses = list(Course.objects.filter(university=university, level='UG').values('course_id', 'title'))
        records = match_courses(records, courses)
        if options['mapping']:
            try:
                mappings = json.loads(Path(options['mapping']).read_text(encoding='utf-8'))
                known = {str(c['course_id']) for c in courses}
                seen = set()
                for item in mappings:
                    key = (item['source_course_name'], item['source_course_code'])
                    if key in seen or item['course_id'] not in known:
                        raise ValueError('Duplicate mapping or course ID outside the selected university/level.')
                    seen.add(key)
                    matching = [r for r in records if (r['course_name'], r['source_course_code']) == key]
                    if not matching:
                        raise ValueError(f'Mapping has no source row: {key}')
                    for row in matching:
                        row['candidate_course_ids'] = [item['course_id']]
                        row['match_status'] = 'explicit_mapping'
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CommandError(f'Invalid mapping: {exc}') from exc
        counts = Counter(r['match_status'] for r in records)
        qualified = sum(bool(r['review_notes']) for r in records)
        code_counts = Counter(r['source_course_code'] for r in records)
        repeated_codes = sum(count > 1 for code, count in code_counts.items() if code)
        self.stdout.write(f'EXTRACTION: {len(records)} fee rows for {year}; {len(courses)} database courses.')
        self.stdout.write(f'Matches: {dict(counts)}. Rows with fee notes: {qualified}. Repeated source codes: {repeated_codes}.')
        selected = records if options['limit'] == 0 else records[:options['limit']]
        for record in selected:
            self.stdout.write(json.dumps(record, ensure_ascii=False))
        if not options['apply']:
            self.stdout.write(self.style.WARNING('Preview only. No database records changed. Use --apply to save eligible matches.'))
            return
        report = apply_records(records)
        self.stdout.write(self.style.SUCCESS(f'Import complete: {report}'))
        self.stdout.write('Unmatched, qualified, conflicting, and duplicate-tuition records were left unchanged.')


def snapshot(tuition):
    
    data = {'tuition_fee': str(tuition.tuition_fee), 'currency': tuition.currency,
            'scholarship': str(tuition.scholarship),
            'tuition_deposit': str(tuition.tuition_deposit) if tuition.tuition_deposit is not None else None}
    try:
        source = tuition.source_metadata
        data['source'] = {key: getattr(source, key) for key in (
            'academic_year', 'source_url', 'source_course_code', 'source_course_name', 'fee_basis', 'raw_fees')}
        data['source']['student_contribution'] = str(source.student_contribution) if source.student_contribution is not None else None
        data['source']['published_total'] = str(source.published_total) if source.published_total is not None else None
    except TuitionSource.DoesNotExist:
        data['source'] = None
    return data


def apply_records(records):
    
    groups = defaultdict(list)
    report = Counter()
    for row in records:
        ids = row['candidate_course_ids']
        if len(ids) != 1:
            report['unmatched_or_ambiguous_rows'] += 1
            continue
        if row['review_notes']:
            report['qualified_rows'] += 1
            continue
        groups[ids[0]].append(row)
    with transaction.atomic():
        for course_id, rows in groups.items():
            signatures = {(r['academic_year'], json.dumps(r['amounts'], sort_keys=True)) for r in rows}
            if len(signatures) != 1:
                report['conflicting_courses'] += 1
                continue
            row = rows[0]
            course = Course.objects.select_for_update().get(pk=course_id)
            for residency, amount_key, basis in [
                ('EU', HEADERS[5], 'tuition_year_1'),
                ('INTL', HEADERS[8], 'published_total_year_1'),
            ]:
                existing = list(Tuition.objects.select_for_update().filter(course=course, residency=residency))
                if len(existing) > 1:
                    report['duplicate_tuition_pairs'] += 1
                    continue
                tuition = existing[0] if existing else None
                amount = row['amounts'][amount_key]
                if amount is None:
                    report['missing_amounts'] += 1
                    continue
                if tuition and tuition.currency != 'EUR' and (tuition.scholarship or tuition.tuition_deposit):
                    report['currency_conflicts'] += 1
                    continue
                before = snapshot(tuition) if tuition else {}
                previous_source = before.get('source')
                if previous_source and int(previous_source['academic_year'][:4]) > int(row['academic_year'][:4]):
                    report['older_years_skipped'] += 1
                    continue
                if tuition is None:
                    tuition = Tuition(course=course, residency=residency, scholarship=0)
                tuition.tuition_fee = Decimal(amount)
                tuition.currency = 'EUR'
                tuition.save()
                TuitionSource.objects.update_or_create(tuition=tuition, defaults={
                    'academic_year': row['academic_year'], 'source_url': row['source_url'],
                    'source_course_code': row['source_course_code'], 'source_course_name': row['course_name'],
                    'fee_basis': basis, 'raw_fees': row['raw_fees'],
                    'student_contribution': Decimal(row['amounts'][HEADERS[6]]),
                    'published_total': Decimal(row['amounts'][HEADERS[7 if residency == 'EU' else 8]]),
                })
                if hasattr(tuition, '_state'):
                    tuition._state.fields_cache.pop('source_metadata', None)
                after = snapshot(tuition)
                if before != after:
                    TuitionUpdateAudit.objects.create(tuition=tuition, course_identifier=course_id,
                        residency=residency, before=before, after=after)
                    report['created' if not before else 'updated'] += 1
                else:
                    report['unchanged'] += 1
    return dict(report)

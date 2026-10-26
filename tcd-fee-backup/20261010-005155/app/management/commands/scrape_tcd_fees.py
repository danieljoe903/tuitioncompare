"""Preview TCD undergraduate fees. This command never writes to the database."""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.core.management.base import BaseCommand, CommandError

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
        parser.add_argument('--academic-year', required=True, help='Example: 2026/27')
        parser.add_argument('--university-id', help='Existing TCD university ID, if slug lookup fails.')
        parser.add_argument('--limit', type=int, default=10, help='Preview row limit; 0 prints all rows.')

    def handle(self, *args, **options):
        from app.models import University, Course
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
        counts = Counter(r['match_status'] for r in records)
        qualified = sum(bool(r['review_notes']) for r in records)
        code_counts = Counter(r['source_course_code'] for r in records)
        repeated_codes = sum(count > 1 for code, count in code_counts.items() if code)
        self.stdout.write(f'PREVIEW ONLY: {len(records)} fee rows for {year}; {len(courses)} database courses.')
        self.stdout.write(f'Matches: {dict(counts)}. Rows with fee notes: {qualified}. Repeated source codes: {repeated_codes}.')
        selected = records if options['limit'] == 0 else records[:options['limit']]
        for record in selected:
            self.stdout.write(json.dumps(record, ensure_ascii=False))
        self.stdout.write(self.style.WARNING(
            'No database records changed. Exact titles are review candidates, not approved fee mappings. '
            'Non-EU amounts retain the source label: total fee for year 1, not independently verified tuition. '
            'Additional levies and eligibility rules must be reviewed separately.'
        ))

"""Preview/apply 11 reviewed Arden campus PG routes and conditional offers.
Run with Django shell: import module; module.run(apply=False/True).
"""
import calendar
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils import timezone as django_timezone
from app.models import Course, Tuition, TuitionSource, TuitionUpdateAudit, Scholarship, ScholarshipRule, DepositPolicy
from app.scraping.uk.arden.calculation import IMPORTER, FACTS, DEADLINES, eligibility_questions

ROOT = Path(settings.BASE_DIR)
BASE = ROOT / 'logs/arden'
YEAR = '2026/27'
TERMS_URL = 'https://arden.ac.uk/terms-and-conditions'
DEPOSIT_URL = 'https://arden.ac.uk/international-students/fees-for-international-students'
IDS = [f'C_UK004_{n}' for n in range(16, 26)] + ['C_UK004_40']


def latest(pattern, filename=None):
    paths = [p for p in BASE.glob(pattern) if (p.is_dir() and (p / filename).is_file())] if filename else list(BASE.glob(pattern))
    if not paths:
        raise ValueError('Required evidence missing: ' + pattern)
    return max(paths, key=lambda p: p.name)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def canonical(value):
    return json.loads(json.dumps(value, default=str))


def source_state(source):
    if source is None:
        return None
    return {key: getattr(source, key) for key in ['academic_year', 'source_url', 'source_course_code', 'source_course_name', 'fee_basis', 'raw_fees']}


def common_conditions():
    fields = {key: [True] for key in FACTS if key not in {
        'arden_student_visa_sponsored', 'arden_programme_at_least_120_credits', 'arden_partner_route'
    }}
    fields['arden_partner_route'] = [False]
    fields.update(course_id=IDS, fee_status=['INTL'], arden_intake=['2026-11', '2027-02'])
    return fields


def run(*, apply=False, preview_path=None):
    preview_path = Path(preview_path) if preview_path else latest('campus-pg-preview-*.json')
    if not preview_path.is_absolute():
        preview_path = ROOT / preview_path
    preview = read(preview_path)
    if preview.get('blocked') or {p['course_id'] for p in preview['proposals']} != set(IDS):
        raise ValueError('Expected the reviewed 11-course preview, without blocked records.')
    course_folder = ROOT / 'logs/arden' / Path(preview['course_evidence']).name
    fee_folder = ROOT / 'logs/arden' / Path(preview['fee_evidence']).name
    courses_evidence = read(course_folder / 'course-evidence.json')
    fees_evidence = read(fee_folder / 'dynamic-fees.json')
    source_path = Path(courses_evidence['source_review'])
    # On Windows this is an absolute path. Cross-host fallbacks use the folder name.
    if not source_path.is_file():
        source_path = latest('source-review-*', 'source-review.json') / 'source-review.json'
    sources = read(source_path)
    terms_page = next(p for p in sources['pages'] if p['source_url'] == TERMS_URL)
    terms = terms_page['text']
    marker = 'Arden University UK Offer for International Students (2026-2027) These terms'
    if terms.count(marker) != 1:
        raise ValueError('Cannot isolate the 2026–2027 international offer terms.')
    start = terms.index(marker)
    early_marker = 'Terms and Conditions for Arden University’s UKI Super Early Bird Offer for November 2026, February 2027 and May 2027 Intakes'
    if terms.count(early_marker) != 1:
        raise ValueError('Cannot isolate the correct early-bird terms.')
    early_start = terms.index(early_marker, start)
    standard_terms = terms[start:early_start]
    early_terms = terms[early_start:]
    for text in ['£17,500', '£14,000', '£13,000', 'November 2026', 'February 2027']:
        if text not in standard_terms:
            raise ValueError('Expected standard offer evidence missing: ' + text)
    for text in ['£3,000', '29th October 2026', '25th January 2027', '17:30', '120 credits']:
        if text not in early_terms:
            raise ValueError('Expected early-bird evidence missing: ' + text)
    deposit_page = next(p for p in sources['pages'] if p['source_url'] == DEPOSIT_URL)
    deposit_note = 'You will be advised of the deposit value for your chosen course during your application.'
    if deposit_note not in deposit_page['text']:
        raise ValueError('Individual deposit advice could not be verified.')
    policy_rules = {
        'schema_version': 1, 'kind': 'application_advised', 'course_ids': IDS,
        'academic_year': YEAR, 'currency': 'GBP',
        'note': 'Arden advises the deposit amount for your chosen course during application. Confirm your individual amount with Arden; no universal numeric deposit has been verified.',
        'eligibility_questions': [],
    }
    now = django_timezone.now()
    writes = 0
    report = {'generated_at': now.isoformat(), 'mode': 'apply' if apply else 'preview',
              'database_changes': False, 'preview_path': str(preview_path), 'courses': [], 'awards': []}
    planned = []

    # Hold course/tuition/source rows consistently while constructing and applying the plan.
    with transaction.atomic():
        course_query = Course.objects.filter(university_id='UK004', course_id__in=IDS)
        tuition_query = Tuition.objects.filter(course__university_id='UK004', course_id__in=IDS, residency='INTL')
        source_query = TuitionSource.objects.filter(tuition__course__university_id='UK004', tuition__course_id__in=IDS, tuition__residency='INTL')
        if apply:
            course_query = course_query.select_for_update()
            tuition_query = tuition_query.select_for_update()
            source_query = source_query.select_for_update()
        db_courses = {c.course_id: c for c in course_query}
        db_tuitions = list(tuition_query)
        db_sources = {s.tuition_id: s for s in source_query}
        for item in preview['proposals']:
            cid = item['course_id']
            course = db_courses.get(cid)
            rows = [t for t in db_tuitions if t.course_id == cid]
            if not course or course.level != 'PG' or len(rows) != 1 or rows[0].currency != 'GBP':
                raise ValueError(cid + ': expected one PG course and GBP INTL tuition.')
            tuition = rows[0]
            source = db_sources.get(tuition.pk)
            if source and source.raw_fees.get('importer') != IMPORTER:
                raise ValueError(cid + ': existing source requires manual review; no overwrite.')
            if not source and (course.title != item['stored_title'] or str(Decimal(str(tuition.tuition_fee)).quantize(Decimal('.01'))) != item['before']['tuition_fee']):
                raise ValueError(cid + ': database differs from reviewed preview.')
            if set(item['campus_options']) != {'Manchester', 'London Stratford'}:
                raise ValueError(cid + ': unexpected campuses.')
            page = next(p for p in courses_evidence['pages'] if p['source_url'] == item['source_course_url'])
            body = (course_folder / Path(page['html_file']).name).read_bytes()
            if hashlib.sha256(body).hexdigest() != page['sha256']:
                raise ValueError(cid + ': course HTML checksum failed.')
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(body, 'html.parser')
            if 'Full-time: 1 year (Blended)' not in page['text']:
                raise ValueError(cid + ': one-year campus tuition basis needs review.')
            selector = soup.find('select', attrs={'name': 'apply_now_course_study_type'})
            if selector is None:
                raise ValueError(cid + ': missing intake selector.')
            option_values = {o.get('value') for o in selector.find_all('option')}
            campus_intakes = {}
            for campus, data in item['campus_options'].items():
                if Decimal(data['gross_annual_tuition']) != Decimal('17500'):
                    raise ValueError(cid + ': unexpected gross annual tuition.')
                campus_intakes[campus] = sorted(data['advertised_full_time_intakes'])
                campus_code = 'bl1666' if campus == 'Manchester' else 'bl1680'
                for intake in campus_intakes[campus]:
                    expected = f"c{item['source_course_id']}|t{campus_code}|i{intake}|m=ft"
                    if intake not in {'2026-11', '2027-02'} or expected not in option_values:
                        raise ValueError(cid + ': campus/intake not supported by saved HTML.')
            fee_hashes = []
            for country in ['Nigeria', 'Ghana']:
                captures = [r for r in fees_evidence['responses'] if r['course_page'] == page['source_url'] and r['country'] == country]
                if len(captures) != 1:
                    raise ValueError(cid + ': missing unique fee capture.')
                capture = captures[0]
                response_bytes = (fee_folder / capture['raw_response_file']).read_bytes()
                if hashlib.sha256(response_bytes).hexdigest() != capture['response_sha256']:
                    raise ValueError(cid + ': fee response checksum failed.')
                if capture['request_parameters']['course_id'] != item['source_course_id']:
                    raise ValueError(cid + ': fee course identifier mismatch.')
                fragment = BeautifulSoup(json.loads(response_bytes)['data'], 'html.parser').get_text(' ', strip=True)
                for expected in ['Manchester Campus Fee per year: £17,500', 'London, Stratford Campus Fee per year: £17,500']:
                    if expected not in fragment:
                        raise ValueError(cid + ': campus annual fee not verified.')
                fee_hashes.append(capture['response_sha256'])
            primary = min(d for dates in campus_intakes.values() for d in dates)
            year, month = map(int, primary.split('-'))
            after_course = dict(location='London Stratford / Manchester', start_month=calendar.month_name[month],
                                start_year=year, intake_months=', '.join(calendar.month_name[m] for m in [11, 2] if any(d.endswith(f'-{m:02d}') for dates in campus_intakes.values() for d in dates)),
                                course_url=item['source_course_url'])
            raw = dict(importer=IMPORTER, verified=True, academic_year=YEAR, currency='GBP',
                       source_fee_category='International', annual_tuition='17500.00', course_kind='postgraduate_taught',
                       study_year=1, default_intake=primary, campus_intakes=campus_intakes,
                       academic_year_basis='Derived from confirmed November 2026/February 2027 intakes using the September–August comparison year; offer cohort is explicitly 2026–2027.',
                       source_course_id=item['source_course_id'], fee_response_sha256=fee_hashes,
                       scholarship_status='verified_rule_eligibility_required', deposit_status='application_advised',
                       scholarship_review={'eligibility_questions': eligibility_questions()},
                       fee_note='Published campus annual gross tuition. Online prices are excluded. Select campus and intake for conditional offer deductions.')
            source_defaults = dict(academic_year=YEAR, source_url=item['countries_checked']['Nigeria']['source_url'],
                                   source_course_code=item['source_course_id'], source_course_name=item['source_heading'],
                                   fee_basis='annual_tuition', raw_fees=raw)
            before = canonical({'course': {key: getattr(course, key) for key in after_course},
                                'tuition': {'tuition_fee': str(Decimal(str(tuition.tuition_fee)).quantize(Decimal('.01'))),
                                            'scholarship': str(tuition.scholarship or 0), 'scholarship_source_id': tuition.scholarship_source_id},
                                'source': source_state(source)})
            after = canonical({'course': after_course, 'tuition': {'tuition_fee': '17500.00', 'scholarship': '0', 'scholarship_source_id': None}, 'source': source_defaults})
            changed = before != after
            planned.append((course, tuition, after_course, source_defaults, before, after, changed))
            report['courses'].append({'course_id': cid, 'changed': changed, 'before': before, 'after': after})
            print(f"{cid}: GBP {before['tuition']['tuition_fee']} -> 17500.00; {after_course['start_month']} {year}; {'update' if changed else 'unchanged'}")

        # Two campus offers and two intake-specific early-bird offers.
        specs = []
        for campus, value in [('Manchester', '4500.00'), ('London Stratford', '3500.00')]:
            conditions = common_conditions()
            conditions['arden_campus'] = [campus]
            specs.append((f'Arden UK International PG Offer — {campus} ({YEAR})', value, conditions))
        for intake in ['2026-11', '2027-02']:
            conditions = common_conditions()
            conditions.update(arden_intake=[intake], arden_campus=['Manchester', 'London Stratford'],
                              arden_student_visa_sponsored=[True], arden_programme_at_least_120_credits=[True],
                              arden_unconditional_offer_on_time=[True], arden_deposit_paid_on_time=[True])
            specs.append((f'Arden UK International PG Early Bird — {intake} ({YEAR})', '3000.00', conditions))
        award_rows = []
        for name, value, conditions in specs:
            matches = list(Scholarship.objects.filter(university_id='UK004', name=name))
            if len(matches) > 1:
                raise ValueError('Duplicate managed scholarship: ' + name)
            scholarship = matches[0] if matches else None
            if scholarship:
                existing = ScholarshipRule.objects.filter(scholarship=scholarship).first()
                if not existing or existing.rules.get('importer') != IMPORTER:
                    raise ValueError('Existing scholarship not owned by this importer: ' + name)
            award_rows.append([scholarship, name, value, conditions])
        policy = DepositPolicy.objects.filter(university_id='UK004', academic_year=YEAR).first()
        if policy and policy.rules.get('importer') != IMPORTER:
            raise ValueError('Existing Arden deposit policy requires review; not overwritten.')
        policy_rules['importer'] = IMPORTER
        # Preserve reviewed deposit scopes added by later Arden batches.
        if policy is not None:
            policy_rules["course_ids"] = sorted(
                set(policy_rules["course_ids"]) | set(policy.rules.get("course_ids", []))
            )
        report['policy'] = {'rules': policy_rules, 'source_url': DEPOSIT_URL}

        if apply:
            for row in award_rows:
                if row[0] is None:
                    row[0] = Scholarship.objects.create(university_id='UK004', name=row[1], level='PG', currency='GBP',
                        award_type='Fixed tuition reduction', award_value=row[2], deadline=None, url=TERMS_URL,
                        eligibility='Conditional UK Student visa/blended-learning offer; see saved calculation rule.',
                        requirements='Campus, intake and all required eligibility facts must be confirmed.',
                        description='Compared first study year only. UK offer may combine with eligible Super Early Bird offer.')
                    writes += 1
            for index, (scholarship, name, value, conditions) in enumerate(award_rows):
                compatible = [str(r[0].pk) for j, r in enumerate(award_rows) if (index < 2) != (j < 2)]
                rule = dict(importer=IMPORTER, kind='fixed', value=value, currency='GBP', benefit='tuition', academic_year=YEAR,
                            study_years=[1], conditions=conditions, award_status='conditional', stacking='explicit',
                            compatible_ids=compatible, percentage_basis='original_tuition', fee_basis_confirmed=True,
                            eligibility_questions=eligibility_questions(), source_terms_sha256=hashlib.sha256(standard_terms.encode()).hexdigest(),
                            early_terms_sha256=hashlib.sha256(early_terms.encode()).hexdigest(),
                            scope_note='Only reviewed campus PG courses and their individually advertised intakes; comparison year one.',
                            deadline_note='Early offer issue and deposit payment must both occur by 17:30 Europe/London on the selected intake deadline.',
                            deadlines=DEADLINES)
                previous = ScholarshipRule.objects.filter(scholarship=scholarship).first()
                if previous is None or previous.rules != rule or not previous.verified or previous.source_url != TERMS_URL:
                    ScholarshipRule.objects.update_or_create(scholarship=scholarship, defaults=dict(rules=rule, verified=True, source_url=TERMS_URL, reviewed_at=now))
                    writes += 1
            policy_defaults = dict(currency='GBP', rules=policy_rules, source_url=DEPOSIT_URL, source_text=deposit_note,
                                   source_sha256=hashlib.sha256(deposit_note.encode()).hexdigest(), verified=True)
            if policy is None or any(getattr(policy, key) != value for key, value in policy_defaults.items()):
                DepositPolicy.objects.update_or_create(university_id='UK004', academic_year=YEAR, defaults={**policy_defaults, 'checked_at': now})
                writes += 1
            for course, tuition, after_course, defaults, before, after, changed in planned:
                if not changed:
                    continue
                for key, value in after_course.items():
                    setattr(course, key, value)
                course.save(update_fields=[*after_course, 'updated_at'])
                tuition.tuition_fee = Decimal('17500.00')
                tuition.scholarship = 0
                tuition.scholarship_source_id = None
                tuition.save(update_fields=['tuition_fee', 'scholarship', 'scholarship_source'])
                TuitionSource.objects.update_or_create(tuition=tuition, defaults=defaults)
                TuitionUpdateAudit.objects.create(tuition=tuition, course_identifier=course.course_id, residency='INTL', before=before, after=after)
                writes += 1
        report['awards'] = [{'name': name, 'value': value, 'conditions': conditions} for _, name, value, conditions in award_rows]
        report['course_changes'] = sum(item[-1] for item in planned)
        report['database_changes'] = bool(writes)
        report['changed_items'] = writes if apply else None
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = BASE / f'campus-pg-import-{stamp}.json'
        # A failure to write the report rolls back the database transaction.
        report['status'] = 'transaction_prepared' if apply else 'preview'
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    report['status'] = 'applied' if apply else 'preview'
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Course changes:', report['course_changes'])
    print('Managed scholarship rules:', len(award_rows))
    print('Deposit: individually advised; no numeric amount assigned.')
    print('Report:', path)
    print('Applied reviewed routes, tuition, rules and policy.' if apply else 'Preview only. No database records changed.')

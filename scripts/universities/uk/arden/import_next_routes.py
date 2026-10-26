"""Preview/apply the 14 reviewed Arden routes, preserving each fee basis."""
import calendar
import hashlib
import json
import re
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from django.db import transaction
from django.utils import timezone as django_timezone
from app.models import Course, Tuition, TuitionSource, TuitionUpdateAudit, Scholarship, ScholarshipRule, DepositPolicy
from app.scraping.uk.arden import calculation as campus
from app.scraping.uk.arden.route_calculation import ONLINE_IMPORTER, TOPUP_IMPORTER, ONLINE_FACTS, eligibility_questions
from scripts.universities.uk.arden.import_campus_postgraduate import BASE, ROOT, YEAR, TERMS_URL, latest, read, canonical, source_state, common_conditions

TOPUP = 'C_ARD004_01'
ONLINE_IDS = ['C_UK004_' + str(n) for n in [36, 37, 38, 53, 64, 65, 66, 67, 68, 69, 70, 71, 72]]
# Published Global Tiers Schedule 1 names these three routes and Schedule 2 confirms their price group.
TIER2 = {'C_UK004_36': ('10550.00', '4747.50', 'MSc Business Psychology'),
         'C_UK004_66': ('10855.00', '4884.75', 'MBA Executive AI'),
         'C_UK004_69': ('8500.00', '3825.00', 'MSc Psychology Conversion Degree (BPS)')}
DURATION = {
    'C_UK004_36': '1 year+ (part-time)', 'C_UK004_37': '1 year+ (part-time)',
    'C_UK004_38': '1 year full-time; 1 year+ part-time', 'C_UK004_53': '1 year+ (part-time)',
    'C_UK004_64': '1 year+ (part-time)', 'C_UK004_65': '1 year+ (part-time)',
    'C_UK004_66': '1 year+ (part-time)', 'C_UK004_67': '1 year+ (part-time)',
    'C_UK004_68': '6 months+ (part-time)', 'C_UK004_69': '15 months (part-time)',
    'C_UK004_70': '10 months+ (part-time)',
    'C_UK004_71': '1 year full-time; 2 years part-time',
    'C_UK004_72': 'Confirmation required', TOPUP: '1 Year',
}


def note_for(cid):
    if cid in TIER2:
        return 'Global Tiers Tier 2 conditions verified for the NG/GH application-country cases in this batch. Other regional tiers and the separate Alumni offer require review.'
    if cid == 'C_UK004_53':
        return 'MBA Top-Up is explicitly excluded from Global Tiers 2026–2027. Other award applicability requires separate review.'
    if cid == 'C_UK004_71':
        return 'LLM is named in Global Tiers Schedule 1, but its applicable net-fee price group has not been confirmed. No deduction applied.'
    return 'An applicable award amount and programme scope have not been confirmed for this route. No deduction applied; this does not establish that no award exists.'


def make_specs():
    specs = []
    for campus_name, value in [('Manchester', '3500.00'), ('London Stratford', '2500.00')]:
        conditions = common_conditions()
        conditions.update(course_id=[TOPUP], arden_campus=[campus_name])
        specs.append(dict(name=f'Arden UK International UG Top-Up — {campus_name} ({YEAR})', level='UG', value=value,
                          conditions=conditions, family='topup_standard', campus=campus_name))
    for intake in ['2026-11', '2027-02']:
        conditions = common_conditions()
        conditions.update(course_id=[TOPUP], arden_campus=['London Stratford'], arden_intake=[intake],
                          arden_student_visa_sponsored=[True], arden_programme_at_least_120_credits=[True],
                          arden_unconditional_offer_on_time=[True], arden_deposit_paid_on_time=[True])
        specs.append(dict(name=f'Arden UK International UG Top-Up Early Bird — London — {intake} ({YEAR})', level='UG',
                          value='3000.00', conditions=conditions, family='topup_early', campus='London Stratford'))
    for cid, (gross, net, label) in TIER2.items():
        conditions = {key: [True] for key in ONLINE_FACTS if key != 'arden_partner_route'}
        conditions.update(arden_partner_route=[False], course_id=[cid], fee_status=['INTL'], arden_campus=['Online'],
                          arden_intake=['2027-01'], arden_online_tier2_country_confirmed=[True], arden_online_gross_quote=[gross])
        specs.append(dict(name=f'Arden Global Tiers Tier 2 — {label} ({YEAR})', level='PG',
                          value=str(Decimal(gross) - Decimal(net)), conditions=conditions, family='online', campus='Online'))
    return specs


def rule_for(spec, specs):
    compatible = []
    if spec['family'] == 'topup_standard' and spec['campus'] == 'London Stratford':
        compatible = [str(s['object'].pk) for s in specs if s['family'] == 'topup_early']
    elif spec['family'] == 'topup_early':
        compatible = [str(s['object'].pk) for s in specs if s['family'] == 'topup_standard' and s['campus'] == 'London Stratford']
    return dict(importer='arden_next_rules_v2', kind='fixed', value=spec['value'], currency='GBP', benefit='tuition',
                academic_year=YEAR, study_years=[1], conditions=spec['conditions'], award_status='conditional',
                stacking='explicit' if compatible else 'exclusive', compatible_ids=compatible,
                percentage_basis='original_tuition', fee_basis_confirmed=True,
                comparison_period='total_programme' if spec['family'] == 'online' else 'annual',
                eligibility_questions=eligibility_questions(raw={'importer': ONLINE_IMPORTER}) if spec['family'] == 'online' else campus.eligibility_questions(),
                scope_note='Only reviewed course IDs and published intake/price basis. Online awards cannot combine with campus offers.',
                review_note='Manchester top-up early-bird combination remains blocked because the published combined price conflicts with arithmetic.' if spec['campus'] == 'Manchester' else '')


def run(*, apply=False, accept_mode_corrections=False, preview_path=None):
    if apply and not accept_mode_corrections:
        raise ValueError('Review the route/mode preview, then apply with accept_mode_corrections=True.')
    path = Path(preview_path) if preview_path else latest('next-route-preview-*.json')
    if not path.is_absolute():
        path = ROOT / path
    preview = read(path)
    if preview.get('blocked') or {r['course_id'] for r in preview['proposals']} != set([TOPUP, *ONLINE_IDS]):
        raise ValueError('Expected the reviewed 14-route preview without blocked items.')
    course_folder = BASE / Path(preview['course_evidence']).name
    fee_folder = BASE / Path(preview['fee_evidence']).name
    pages = read(course_folder / 'course-evidence.json')
    fees = read(fee_folder / 'dynamic-fees.json')
    source_path = Path(pages['source_review'])
    if not source_path.is_file():
        source_path = latest('source-review-*', 'source-review.json') / 'source-review.json'
    sources = read(source_path)
    terms = next(p['text'] for p in sources['pages'] if p['source_url'] == TERMS_URL)
    start = terms.index('Arden University Global Tiers Offer 2026 - 2027 – Online Distance Learning')
    end = terms.index('Terms and Conditions for Arden University’s UKI Super Early Bird Offer for September 2026 Intake', start)
    online_terms = terms[start:end]
    schedule_start = online_terms.index('Schedule 1 – Eligible Programmes')
    schedule_end = online_terms.index('Schedule 2 –', schedule_start)
    eligible_names = online_terms[schedule_start:schedule_end]
    for gross, net, label in TIER2.values():
        if label not in eligible_names or ('£' + f'{Decimal(net):,.2f}') not in online_terms:
            raise ValueError('Global Tiers programme/amount not confirmed: ' + label)
    for required in ['January 2027', 'April 2027', 'Ghana', 'Nigeria', '*Excluding MBA Top-Up']:
        if required not in online_terms:
            raise ValueError('Missing Global Tiers evidence: ' + required)
    report = dict(generated_at=django_timezone.now().isoformat(), mode='apply' if apply else 'preview', database_changes=False,
                  preview_path=str(path), courses=[], awards=[], review_notes=[
                      'Programme totals require comparison_basis=programme_total; they are not annual prices.',
                      'Online quotes in this batch are verified for Nigeria/Ghana. Other fee countries need review.',
                      'Course C_UK004_72 has a part-time application option but only a full-time duration summary. Duration remains confirmation required.',
                      'Separate Alumni offer not yet reconciled for these routes.'])
    writes = 0
    now = django_timezone.now()
    specs = make_specs()
    with transaction.atomic():
        course_q = Course.objects.filter(university_id='UK004', course_id__in=[TOPUP, *ONLINE_IDS])
        tuition_q = Tuition.objects.filter(course__university_id='UK004', course_id__in=[TOPUP, *ONLINE_IDS], residency='INTL')
        source_q = TuitionSource.objects.filter(tuition__course__university_id='UK004', tuition__course_id__in=[TOPUP, *ONLINE_IDS], tuition__residency='INTL')
        if apply:
            course_q = course_q.select_for_update()
            tuition_q = tuition_q.select_for_update()
            source_q = source_q.select_for_update()
        db_courses = {c.course_id: c for c in course_q}
        tuitions = list(tuition_q)
        db_sources = {s.tuition_id: s for s in source_q}
        plans = []
        for item in preview['proposals']:
            cid = item['course_id']
            course = db_courses.get(cid)
            rows = [t for t in tuitions if t.course_id == cid]
            if course is None or len(rows) != 1 or rows[0].currency != 'GBP':
                raise ValueError(cid + ': unique GBP INTL tuition required.')
            tuition = rows[0]
            existing_source = db_sources.get(tuition.pk)
            if existing_source and existing_source.raw_fees.get('importer') not in {ONLINE_IMPORTER, TOPUP_IMPORTER}:
                raise ValueError(cid + ': existing source not owned by this importer; review required.')
            if not existing_source and (course.title != item['stored_title'] or course.location != item['stored_location'] or Decimal(str(tuition.tuition_fee)) != Decimal(item['stored_tuition'])):
                raise ValueError(cid + ': database differs from the reviewed route preview.')
            mode = 'campus' if cid == TOPUP else 'online'
            if item['proposed_mode'] != mode or item['fee_basis'] != ('annual_tuition' if cid == TOPUP else 'total_programme_fee'):
                raise ValueError(cid + ': mode/basis mismatch.')
            page = next(p for p in pages['pages'] if p['source_url'] == item['source_url'])
            body = (course_folder / Path(page['html_file']).name).read_bytes()
            if hashlib.sha256(body).hexdigest() != page['sha256']:
                raise ValueError(cid + ': course HTML checksum failed.')
            soup = BeautifulSoup(body, 'html.parser')
            course_field = soup.find('input', attrs={'name': 'fees_course_id'})
            if not course_field or course_field.get('value') != item['source_course_id']:
                raise ValueError(cid + ': published source course ID mismatch.')
            selector = soup.find('select', attrs={'name': 'apply_now_course_study_type'})
            available = {o.get('value') for o in selector.find_all('option')} if selector else set()
            for option in item['study_options']:
                if option['source_option'] not in available:
                    raise ValueError(cid + ': study option does not match saved HTML.')
                match = re.fullmatch(r'c(\d+)\|t([^|]+)\|i(\d{4}-\d{2})\|m=(ft|pt)', option['source_option'])
                if not match or match.group(1) != item['source_course_id'] or match.group(3) != option['intake'] or ('full_time' if match.group(4) == 'ft' else 'part_time') != option['attendance']:
                    raise ValueError(cid + ': invalid study-option metadata.')
                expected_location = {'dl1668': 'Online', 'bl1666': 'Manchester', 'bl1680': 'London Stratford'}.get(match.group(2))
                if option['location'] != expected_location:
                    raise ValueError(cid + ': invalid location metadata.')
            if not item['study_options']:
                raise ValueError(cid + ': no reviewed study options.')
            gross = str(Decimal(item['published_amount']).quantize(Decimal('.01')))
            hashes = []
            for country in ['Nigeria', 'Ghana']:
                captures = [r for r in fees['responses'] if r['course_page'] == page['source_url'] and r['country'] == country]
                if len(captures) != 1:
                    raise ValueError(cid + ': unique country fee capture required.')
                capture = captures[0]
                response = (fee_folder / capture['raw_response_file']).read_bytes()
                if hashlib.sha256(response).hexdigest() != capture['response_sha256']:
                    raise ValueError(cid + ': fee response checksum failed.')
                if capture['request_parameters']['course_id'] != item['source_course_id']:
                    raise ValueError(cid + ': fee response course ID mismatch.')
                text = BeautifulSoup(json.loads(response)['data'], 'html.parser').get_text(' ', strip=True)
                if cid == TOPUP:
                    for expected in ['Manchester Campus Fee per year: £16,000', 'London, Stratford Campus Fee per year: £16,000',
                                     'Fee with UK Offer for International Students: £12,500', 'Fee with UK Offer for International Students: £13,500']:
                        if expected not in text:
                            raise ValueError(cid + ': campus top-up quote missing.')
                    if gross != '16000.00':
                        raise ValueError('Unexpected top-up gross.')
                else:
                    amounts = re.findall(r'Studying Online Fee Total fee:\s*£([\d,]+(?:\.\d{2})?)', text)
                    if len(amounts) != 1 or Decimal(amounts[0].replace(',', '')) != Decimal(gross):
                        raise ValueError(cid + ': programme-total quote mismatch.')
                hashes.append(capture['response_sha256'])
            dates = sorted({o['intake'] for o in item['study_options']})
            if cid == TOPUP and dates != ['2026-11', '2027-02']:
                raise ValueError('Unexpected campus top-up intakes.')
            if cid != TOPUP and any(d not in {'2027-01', '2027-04'} for d in dates):
                raise ValueError(cid + ': unexpected online intake year.')
            primary = dates[0]
            year, month = map(int, primary.split('-'))
            duration_match = re.search(r'Duration (.*?)(?: Apply today| Study abroad| Speak to| Register interest)', page['text'])
            if not duration_match:
                raise ValueError(cid + ': missing duration evidence.')
            duration_text = duration_match.group(1)
            expected_duration = DURATION[cid]
            if cid == TOPUP and 'Full-time: 1 year (Blended)' not in duration_text:
                raise ValueError('Top-up one-year basis not confirmed.')
            after_course = dict(location='London Stratford / Manchester' if cid == TOPUP else 'Online',
                                start_month=calendar.month_name[month], start_year=year,
                                intake_months=', '.join(dict.fromkeys(calendar.month_name[int(d[-2:])] for d in dates)),
                                course_url=item['source_url'], duration=expected_duration)
            for key, value in after_course.items():
                max_length = Course._meta.get_field(key).max_length
                if max_length and isinstance(value, str) and len(value) > max_length:
                    raise ValueError(cid + ': published ' + key + ' exceeds model field length; review required.')
            raw = dict(importer=TOPUP_IMPORTER if cid == TOPUP else ONLINE_IMPORTER,
                       verified=True, academic_year=YEAR, currency='GBP', source_fee_category='International',
                       course_kind='undergraduate_topup' if cid == TOPUP else 'postgraduate_taught_online',
                       study_mode=mode, default_intake=primary, study_options=item['study_options'], study_year=1,
                       course_source_url=item['source_url'], source_course_id=item['source_course_id'], fee_response_sha256=hashes,
                       academic_year_basis='Derived from explicitly advertised 2026/2027 intakes using the September–August comparison year; offer terms identify cohort 2026–2027.',
                       published_duration_summary=duration_text, duration_verified=cid != 'C_UK004_72',
                       scholarship_status='verified_rule_eligibility_required' if cid in TIER2 or cid == TOPUP else 'applicability_requires_review',
                       scholarship_review={'reason': note_for(cid) if cid != TOPUP else 'Standard campus offer verified; Manchester early-bird combination held for source discrepancy.',
                                           'eligibility_questions': eligibility_questions(raw={'importer': ONLINE_IMPORTER}) if cid != TOPUP else campus.eligibility_questions()},
                       deposit_status='campus_policy_not_applicable' if cid != TOPUP else 'application_advised')
            if cid == TOPUP:
                raw.update(annual_tuition=gross, campus_intakes={loc: sorted({o['intake'] for o in item['study_options'] if o['location'] == loc}) for loc in ['Manchester', 'London Stratford']},
                           fee_note='Annual gross campus top-up tuition, one-year full-time route. UK offers conditional. Manchester early-bird combination remains unverified.')
            else:
                raw.update(total_programme_fee=gross, online_intakes=dates, fee_country_scope=['NG', 'GH'],
                           fee_note='Total online programme fee, not an annual charge. Do not divide by duration or compare directly with annual tuition. ' + note_for(cid))
                if cid in TIER2 and gross != TIER2[cid][0]:
                    raise ValueError(cid + ': Global Tiers gross quote changed; rule basis needs review.')
            defaults = dict(academic_year=YEAR, source_url=item['country_evidence'][0]['source_url'], source_course_code=item['source_course_id'],
                            source_course_name=item['source_heading'], fee_basis=item['fee_basis'], raw_fees=raw)
            before = canonical({'course': {key: getattr(course, key) for key in after_course},
                                'tuition': {'tuition_fee': str(Decimal(str(tuition.tuition_fee)).quantize(Decimal('.01'))), 'scholarship': str(tuition.scholarship or 0), 'scholarship_source_id': tuition.scholarship_source_id},
                                'source': source_state(existing_source)})
            after = canonical({'course': after_course, 'tuition': {'tuition_fee': gross, 'scholarship': '0', 'scholarship_source_id': None}, 'source': defaults})
            changed = before != after
            plans.append((course, tuition, after_course, defaults, before, after, changed))
            report['courses'].append({'course_id': cid, 'changed': changed, 'before': before, 'after': after})
            print(f"{cid}: GBP {before['tuition']['tuition_fee']} -> {gross}; {mode}; {item['fee_basis']} — {'update' if changed else 'unchanged'}")
        policy = DepositPolicy.objects.filter(university_id='UK004', academic_year=YEAR).first()
        if not policy or not policy.verified or policy.rules.get('kind') != 'application_advised' or policy.rules.get('importer') != campus.IMPORTER:
            raise ValueError('Expected verified first-batch campus deposit policy; no overwrite performed.')
        policy_rules = {**policy.rules, 'course_ids': sorted(set(policy.rules.get('course_ids', [])) | {TOPUP})}
        for spec in specs:
            rows = list(Scholarship.objects.filter(university_id='UK004', name=spec['name']))
            if len(rows) > 1:
                raise ValueError('Duplicate managed scholarship: ' + spec['name'])
            spec['object'] = rows[0] if rows else None
            if rows:
                old_rule = ScholarshipRule.objects.filter(scholarship=rows[0]).first()
                if not old_rule or old_rule.rules.get('importer') != 'arden_next_rules_v2':
                    raise ValueError('Existing scholarship requires review: ' + spec['name'])
        if apply:
            for spec in specs:
                if spec['object'] is None:
                    spec['object'] = Scholarship.objects.create(university_id='UK004', name=spec['name'], level=spec['level'], currency='GBP',
                        award_type='Fixed tuition reduction', award_value=spec['value'], deadline=None, url=TERMS_URL,
                        eligibility='Verified published offer scope with required individual eligibility conditions.', requirements='All saved conditions must be confirmed.',
                        description='Programme-total reduction for online study; annual reduction for the one-year campus top-up. See saved structured rule.')
                    writes += 1
            for spec in specs:
                rules = rule_for(spec, specs)
                rules['source_terms_sha256'] = hashlib.sha256((online_terms if spec['family'] == 'online' else terms[terms.index('Arden University UK Offer for International Students (2026-2027) These terms'):]).encode()).hexdigest()
                old_rule = ScholarshipRule.objects.filter(scholarship=spec['object']).first()
                if not old_rule or old_rule.rules != rules or not old_rule.verified or old_rule.source_url != TERMS_URL:
                    ScholarshipRule.objects.update_or_create(scholarship=spec['object'], defaults=dict(verified=True, source_url=TERMS_URL, rules=rules, reviewed_at=now))
                    writes += 1
            if policy.rules != policy_rules:
                policy.rules = policy_rules
                policy.checked_at = now
                policy.save(update_fields=['rules', 'checked_at', 'updated_at'])
                writes += 1
            for course, tuition, after_course, defaults, before, after, changed in plans:
                if not changed:
                    continue
                for key, value in after_course.items():
                    setattr(course, key, value)
                course.save(update_fields=[*after_course, 'updated_at'])
                tuition.tuition_fee = Decimal(after['tuition']['tuition_fee'])
                tuition.scholarship = 0
                tuition.scholarship_source_id = None
                tuition.save(update_fields=['tuition_fee', 'scholarship', 'scholarship_source'])
                TuitionSource.objects.update_or_create(tuition=tuition, defaults=defaults)
                TuitionUpdateAudit.objects.create(tuition=tuition, course_identifier=course.course_id, residency='INTL', before=before, after=after)
                writes += 1
        report['awards'] = [{key: spec[key] for key in ['name', 'level', 'value', 'conditions', 'family']} for spec in specs]
        report['course_changes'] = sum(p[-1] for p in plans)
        report['database_changes'] = bool(writes)
        report['changed_items'] = writes if apply else None
        report['campus_deposit_scope'] = len(policy_rules['course_ids'])
        report['status'] = 'transaction_prepared' if apply else 'preview'
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        output = BASE / f'next-routes-import-{stamp}.json'
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    report['status'] = 'applied' if apply else 'preview'
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Course changes:', report['course_changes'])
    print('Managed scholarship rules:', len(specs), '(4 campus top-up; 3 online Tier 2)')
    print('Campus deposit scope:', report['campus_deposit_scope'])
    print('Online numeric deposits: none assigned; campus pre-CAS policy does not apply.')
    print('Manchester top-up early-bird combination: pending discrepancy review.')
    print('Report:', output)
    print('Applied reviewed routes, fee bases and rules.' if apply else 'Preview only. No database records changed.')

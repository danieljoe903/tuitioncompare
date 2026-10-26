"""Preview/apply verified online Postgraduate Alumni scope and questions.
No gross tuition, numeric scholarships, courses, deposits or EU fees changed.
"""
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils.timezone import now
from app.models import Course, Tuition, TuitionSource, Scholarship, ScholarshipRule, TuitionUpdateAudit
from app.scraping.uk.arden import route_calculation as adapter
from scripts.universities.uk.arden.import_campus_postgraduate import latest, read, TERMS_URL

OWNER = 'arden_online_alumni_v7'
NAME = 'Arden 50% Postgraduate Alumni Offer — Online (2026/27)'
ROOT = Path(settings.BASE_DIR)
COMMON = ['arden_account_in_good_standing', 'arden_not_blocked_or_expelled',
          'arden_no_staff_agent_connection', 'arden_application_accurate', 'arden_not_sanctioned']


def evidence():
    doc = read(latest('source-review-*', 'source-review.json') / 'source-review.json')
    text = next(p['text'] for p in doc['pages'] if p['source_url'] == TERMS_URL)
    start = text.index('Arden University 50% Postgraduate Alumni Offer (Online)')
    # The first occurrence is the online section, not the Berlin/home-student variants.
    section = text[start:]
    # End after clause 8.1, which includes the exclusive/best-offer rule.
    end = section.index('8.2.')
    section = section[:end]
    required = ['January 2023', 'including Top-Ups and PG Certificates',
                'online distance learning', '2:2 and 300 credits', 'five years',
                'official results', 'educational advisor', 'If you are granted',
                'not to be combined', 'greatest discount']
    normalized = section.replace('\u00a0', ' ')
    for phrase in required:
        if phrase not in normalized:
            raise ValueError('Online Alumni evidence needs review: ' + phrase)
    if 'does not apply to international students' in normalized:
        raise ValueError('Wrong Alumni terms section selected.')
    return section


def run(*, apply=False):
    if not hasattr(adapter, 'ALUMNI_FACTS'):
        raise ValueError('Run connect_online_alumni.py --apply first, then start a fresh Django shell.')
    section = evidence()
    scope = json.loads((ROOT / 'data/universities/uk/arden/online_alumni_scope.json').read_text(encoding='utf-8'))
    if len(scope) != 13 or len({p['course_id'] for p in scope}) != 13:
        raise ValueError('Expected the reviewed 13 online postgraduate routes.')
    conditions = {k: [True] for k in [*COMMON, *adapter.ALUMNI_FACTS]}
    conditions.update(course_id=sorted(p['course_id'] for p in scope), fee_status=['INTL'],
                      arden_campus=['Online'], arden_partner_route=[False],
                      arden_online_offer_confirmed=[True], arden_intake=['2027-01', '2027-04'])
    questions = adapter.eligibility_questions(raw={'importer': adapter.ONLINE_IMPORTER})
    rules = dict(importer=OWNER, kind='percentage', value='50.00', currency='GBP', benefit='tuition',
                 academic_year='2026/27', study_years=[1], conditions=conditions,
                 award_status='conditional', stacking='exclusive', compatible_ids=[],
                 percentage_basis='original_tuition', comparison_period='total_programme', fee_basis_confirmed=True,
                 eligibility_questions=questions, source_terms_sha256=hashlib.sha256(section.encode()).hexdigest(),
                 scope_note='Reviewed online postgraduate routes only. Programme total, not annual tuition. Online Alumni conditions apply; no UK home-student restriction imported from the separate campus offer.',
                 review_note='An Alumni award must be granted. Cannot combine with Global Tiers; the greatest eligible exclusive offer is selected.')
    report = dict(mode='apply' if apply else 'preview', courses=[], rule=rules, database_changes=False)
    with transaction.atomic():
        awards = list(Scholarship.objects.select_for_update().filter(university_id='UK004', name=NAME))
        if len(awards) > 1:
            raise ValueError('Duplicate online Alumni scholarship records.')
        award = awards[0] if awards else None
        existing = ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None
        if award and (not existing or existing.rules.get('importer') != OWNER):
            raise ValueError('Existing Alumni scholarship requires ownership review.')
        rule_changed = existing is None or existing.rules != rules or not existing.verified or existing.source_url != TERMS_URL
        plans = []
        for item in scope:
            cid = item['course_id']
            course = Course.objects.select_for_update().get(university_id='UK004', course_id=cid)
            tuition = Tuition.objects.select_for_update().get(course_id=cid, residency='INTL')
            source = TuitionSource.objects.select_for_update().get(tuition=tuition)
            raw = source.raw_fees
            if (not course.is_active or course.title != item['title'] or course.location != 'Online'
                    or raw.get('verified') is not True or raw.get('importer') != adapter.ONLINE_IMPORTER
                    or source.academic_year != '2026/27' or source.fee_basis != 'total_programme_fee'
                    or source.source_course_code != item['source_course_code'] or tuition.currency != 'GBP'
                    or Decimal(str(tuition.tuition_fee)) != Decimal(item['gross'])
                    or Decimal(str(raw.get('total_programme_fee', '0'))) != Decimal(item['gross'])
                    or sorted(raw.get('online_intakes', [])) != sorted(item['intakes'])
                    or set(raw.get('fee_country_scope', [])) != {'NG', 'GH'}):
                raise ValueError(cid + ': route, gross, fee basis or intake changed; no update.')
            after = deepcopy(raw)
            after['online_alumni_review'] = dict(importer=OWNER, status='verified_rule_eligibility_required',
                source_url=TERMS_URL, percentage='50.00', comparison_period='total_programme',
                net_if_eligible=str((Decimal(item['gross']) / 2).quantize(Decimal('.01'))),
                note='Online Alumni offer verified separately from Global Tiers. All personal eligibility facts and grant confirmation are required; no stacking.',
                eligibility_questions=questions)
            after['scholarship_status'] = 'verified_rule_eligibility_required'
            after['scholarship_review'] = dict(reason='At least the conditional online Alumni offer is verified. Existing Global Tiers awards remain separate; other offer reviews may remain pending.', eligibility_questions=questions)
            changed = raw != after
            plans.append((tuition, source, raw, after, changed))
            report['courses'].append(dict(course_id=cid, gross=item['gross'], net_if_alumni_eligible=after['online_alumni_review']['net_if_eligible'], changed=changed))
        changed_items = int(rule_changed) + sum(p[-1] for p in plans)
        report.update(changed_items=changed_items, database_changes=bool(apply and changed_items))
        path = ROOT / 'logs/arden' / ('online-alumni-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        if apply and changed_items:
            if award is None:
                award = Scholarship.objects.create(university_id='UK004', name=NAME, level='PG', currency='GBP',
                    award_type='Percentage tuition reduction', award_value='50', deadline=None, url=TERMS_URL,
                    eligibility='Qualifying Arden undergraduate alumni studying a reviewed online postgraduate route.',
                    requirements='2:2 and 300 credits; graduation within five years; official results; advisor informed; award granted; all saved exclusions checked.',
                    description='50% off the original programme tuition. Exclusive; no stacking.')
            if rule_changed:
                ScholarshipRule.objects.update_or_create(scholarship=award, defaults=dict(verified=True, rules=rules, source_url=TERMS_URL, reviewed_at=now()))
            for tuition, source, before, after, changed in plans:
                if changed:
                    source.raw_fees = after
                    source.save(update_fields=['raw_fees'])
                    TuitionUpdateAudit.objects.create(tuition=tuition, course_identifier=tuition.course_id, residency='INTL', before={'raw_fees': before}, after={'raw_fees': after, 'scholarship_rule_importer': OWNER})
    for row in report['courses']:
        print(row['course_id'] + ': Alumni net GBP ' + row['net_if_alumni_eligible'] + ' if eligible.')
    print('Online postgraduate scope: 13; newly covered pending courses: 9.')
    print('Changed/proposed items:', changed_items)
    print('Report:', path)
    print('Saved conditional online Alumni scope and questions.' if apply else 'Preview only. No database records changed.')
    print('Gross tuition, numeric stored discounts, deposit policies and existing award rules unchanged.')
    return report

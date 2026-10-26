"""Add reviewed Finance pathway to the existing Executive MBA AI Tier 2 rule.
Preview is default. Run through manage.py shell. No tuition/deposit writes.
"""
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from datetime import datetime, timezone
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils.timezone import now
from app.models import Course, Tuition, TuitionSource, Scholarship, ScholarshipRule, TuitionUpdateAudit
from scripts.universities.uk.arden.import_campus_postgraduate import latest, read, TERMS_URL
from app.scraping.uk.arden.route_calculation import ONLINE_IMPORTER, ONLINE_FACTS

CID = 'C_UK004_67'
DONOR = 'C_UK004_66'
BATCH = 'arden_finance_award_v6'
TITLE = 'Executive MBA (Finance)'
GROSS = Decimal('10855.00')
NET = Decimal('4884.75')


def validate_rule(rule, section):
    r = rule.rules
    expected = dict(kind='fixed', value='5970.25', benefit='tuition', currency='GBP',
                    academic_year='2026/27', comparison_period='total_programme',
                    award_status='conditional', stacking='exclusive', study_years=[1],
                    compatible_ids=[], fee_basis_confirmed=True)
    for key, value in expected.items():
        if r.get(key) != value:
            raise ValueError('Existing rule changed: ' + key)
    c = r.get('conditions', {})
    required = {k: [True] for k in ONLINE_FACTS if k != 'arden_partner_route'}
    required.update(arden_partner_route=[False], fee_status=['INTL'], arden_campus=['Online'],
                    arden_intake=['2027-01'], arden_online_tier2_country_confirmed=[True],
                    arden_online_gross_quote=['10855.00'])
    if any(c.get(k) != v for k, v in required.items()):
        raise ValueError('Required eligibility guards changed.')
    if set(c.get('course_id', [])) not in ({DONOR}, {DONOR, CID}):
        raise ValueError('Unexpected Executive MBA rule scope.')
    if not r.get('eligibility_questions'):
        raise ValueError('Saved eligibility questions missing.')
    if r.get('source_terms_sha256') != sha256(section.encode()).hexdigest():
        raise ValueError('Saved terms differ from the reviewed donor evidence; review before applying.')


def run(*, apply=False):
    folder = latest('source-review-*', 'source-review.json')
    pages = read(folder / 'source-review.json')['pages']
    terms = next(p['text'] for p in pages if p['source_url'] == TERMS_URL)
    start = terms.index('Arden University Global Tiers Offer 2026 - 2027 – Online Distance Learning')
    end = terms.index('Terms and Conditions for Arden University’s UKI Super Early Bird Offer for September 2026 Intake', start)
    section = terms[start:end]
    schedule = section.split('Schedule 1 – Eligible Programmes', 1)[1].split('Schedule 2', 1)[0]
    for text in ['MBA (Executive)', 'including themed routes/pathways', 'LLM']:
        if text not in schedule:
            raise ValueError('Missing eligible-programme evidence: ' + text)
    for text in ['£4,884.75', 'January 2027', 'Nigeria', 'Ghana']:
        if text not in section:
            raise ValueError('Missing price/cohort/country evidence: ' + text)
    with transaction.atomic():
        course = Course.objects.select_for_update().get(university_id='UK004', course_id=CID)
        tuition = Tuition.objects.select_for_update().get(course_id=CID, residency='INTL')
        source = TuitionSource.objects.select_for_update().get(tuition=tuition)
        raw = source.raw_fees
        if (not course.is_active or course.title != TITLE or course.location != 'Online'
                or source.source_course_code != '2343' or source.academic_year != '2026/27'
                or source.fee_basis != 'total_programme_fee' or raw.get('verified') is not True
                or raw.get('importer') != ONLINE_IMPORTER or tuition.currency != 'GBP'
                or Decimal(str(tuition.tuition_fee)) != GROSS
                or Decimal(str(raw.get('total_programme_fee', '0'))) != GROSS
                or set(raw.get('online_intakes', [])) != {'2027-01'}
                or set(raw.get('fee_country_scope', [])) != {'NG', 'GH'}):
            raise ValueError('Finance route, fee, country or intake metadata changed; no update.')
        rules = list(ScholarshipRule.objects.select_for_update().filter(scholarship__university_id='UK004'))
        donors = [r for r in rules if r.verified and r.rules.get('importer') == 'arden_next_rules_v2'
                  and DONOR in r.rules.get('conditions', {}).get('course_id', [])]
        if len(donors) != 1:
            raise ValueError('Expected one verified Executive MBA AI Tier 2 donor rule.')
        donor = donors[0]
        validate_rule(donor, section)
        for r in rules:
            if r.pk != donor.pk and r.rules.get('importer') != BATCH and r.verified and CID in r.rules.get('conditions', {}).get('course_id', []):
                raise ValueError('Another Finance award exists; stacking review required.')
        name = 'Arden Global Tiers Tier 2 — Executive MBA Finance (2026/27)'
        awards = list(Scholarship.objects.select_for_update().filter(university_id='UK004', name=name))
        if len(awards) > 1:
            raise ValueError('Duplicate Finance scholarship records.')
        award = awards[0] if awards else None
        existing = next((r for r in rules if award and r.scholarship_id == award.pk), None)
        if award and (not existing or existing.rules.get('importer') != BATCH):
            raise ValueError('Existing Finance scholarship is not owned by this correction.')
        before_rule = deepcopy(existing.rules) if existing else None
        after_rule = deepcopy(donor.rules)
        after_rule['importer'] = BATCH
        after_rule['conditions']['course_id'] = [CID]
        after_rule['finance_pathway_review'] = dict(importer=BATCH, source_url=TERMS_URL,
            source_course_id='2343', cohort='January 2027', gross='10855.00', net='4884.75',
            rationale='Executive MBA Finance is a themed Executive MBA route; Schedule 1 includes postgraduate themed routes/pathways. Schedule 2 MBA Tier 2 price applies.')
        after_raw = deepcopy(raw)
        after_raw['scholarship_status'] = 'verified_rule_eligibility_required'
        after_raw['scholarship_review'] = dict(reason='Conditional Global Tiers Tier 2 Executive MBA Finance award. NG/GH cases only; total programme basis, not annual.',
            eligibility_questions=deepcopy(after_rule['eligibility_questions']))
        after_raw['finance_award_review'] = dict(importer=BATCH, gross='10855.00', deduction='5970.25', net_if_eligible='4884.75')
        changed = int(before_rule != after_rule or (existing and (not existing.verified or existing.source_url != TERMS_URL))) + int(raw != after_raw)
        report = dict(mode='apply' if apply else 'preview', course_id=CID, changed_items=changed,
            gross='10855.00', conditional_deduction='5970.25', net_if_eligible='4884.75',
            before_rule=before_rule, after_rule=after_rule,
            llm_status='Eligible programme listed; applicable LLM price group requires confirmation. No new LLM deduction.',
            database_changes=bool(apply and changed))
        output = Path(settings.BASE_DIR) / 'logs/arden' / ('finance-award-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        # Write the reviewable report before committing any changes.
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        if apply and changed:
            if award is None:
                award = Scholarship.objects.create(university_id='UK004', name=name, level='PG', currency='GBP',
                    award_type='Fixed tuition reduction', award_value='5970.25', deadline=None, url=TERMS_URL,
                    eligibility='Verified online Executive MBA Finance Tier 2 scope; all saved conditions required.',
                    requirements='NG/GH eligibility and all personal conditions must be confirmed.',
                    description='Conditional programme-total deduction. No annual allocation or stacking.')
            ScholarshipRule.objects.update_or_create(scholarship=award, defaults=dict(
                verified=True, source_url=TERMS_URL, rules=after_rule, reviewed_at=now()))
            source.raw_fees = after_raw
            source.save(update_fields=['raw_fees'])
            TuitionUpdateAudit.objects.create(tuition=tuition, course_identifier=CID, residency='INTL',
                before={'scholarship_rule': before_rule, 'source_raw_fees': raw},
                after={'scholarship_rule': after_rule, 'source_raw_fees': after_raw})
    print('Executive MBA Finance: GBP 10855.00 - 5970.25 = 4884.75 if eligible.')
    print('Changed/proposed items:', changed)
    print('LLM: price-group confirmation pending; no new deduction.')
    print('Report:', output)
    print('Saved rule scope and review metadata.' if apply else 'Preview only. No database records changed.')
    print('Gross tuition, stored numeric discounts, deposit policies and eligibility questions preserved.')
    return report

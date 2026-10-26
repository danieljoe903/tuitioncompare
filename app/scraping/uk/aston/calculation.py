"""Reviewed Aston fees; unsupported legacy awards/deposits stay excluded."""
import json
from decimal import Decimal
from pathlib import Path
from functools import lru_cache
from copy import copy
from django.conf import settings
from app.scholarship_calculation import calculate_scholarships
OWNER='aston_first30_v1'

@lru_cache(maxsize=1)
def manifest():
    return {m['course_id']:m for m in json.loads((Path(settings.BASE_DIR)/'data/universities/uk/aston/reviewed_first30.json').read_text())}

@lru_cache(maxsize=1)
def award_manifest():
    extra={m['course_id']:m for m in json.loads((Path(settings.BASE_DIR)/'data/universities/uk/aston/reviewed_remaining14.json').read_text())}
    if set(extra).intersection(manifest()):raise ValueError('Aston reviewed fee manifests overlap.')
    return {**manifest(),**extra}

@lru_cache(maxsize=1)
def pending_fee_manifest():
    return {m['course_id']:m for m in json.loads((Path(settings.BASE_DIR)/'data/universities/uk/aston/reviewed_pending_routes.json').read_text())}

@lru_cache(maxsize=1)
def all_fee_manifest():
    return {**award_manifest(),**pending_fee_manifest()}


def follow_up_message(course_id):
    path=Path(settings.BASE_DIR)/'data/universities/uk/aston/review_followups.json'
    if not path.is_file():return None
    data=json.loads(path.read_text(encoding='utf-8'))
    return data.get('routes',{}).get(course_id,{}).get('message')

def fee_error(tuition,source):
    c=tuition.course;m=all_fee_manifest().get(c.course_id)
    raw=source.raw_fees if source and isinstance(source.raw_fees,dict) else {}
    if not m:
        return follow_up_message(c.course_id) or 'Aston exact route, intake and international fee require verification. No legacy amount is displayed.'
    if raw.get('importer')!=('aston_pending_routes_v1' if c.course_id in pending_fee_manifest() else OWNER if c.course_id in manifest() else 'aston_remaining14_v1') or raw.get('verified') is not True:
        return 'Aston exact route, intake and international fee require verification. No legacy amount is displayed.'
    if (str(c.university_id)!='UK007' or not c.is_active or c.level!=m['level']
        or c.start_year!=2027 or c.start_month!=m['start_month'] or c.course_url!=m['source_url']
        or c.duration!=raw.get('stored_duration') or c.location!=raw.get('stored_location')
        or tuition.residency!='INTL' or tuition.currency!='GBP'
        or Decimal(str(tuition.tuition_fee))!=Decimal(m['tuition_fee'])
        or source.academic_year!=m['academic_year'] or source.fee_basis!=m['fee_basis']
        or source.source_url!=m['source_url'] or raw.get('source_sha256')!=m['source_sha256']):
        return 'Aston saved fee, course or intake differs from the reviewed source.'
    if c.course_id not in manifest() and c.title!=raw.get('source_course_name'):
        return 'Aston replacement or course title differs from the audited import.'
    return None

def fee_metadata(source,raw):
    return dict(fee_basis=source.fee_basis,academic_year=source.academic_year,
        fee_label='Full-time course fee' if source.fee_basis=='full_time_course_fee' else 'First-year tuition fee' if source.fee_basis=='first_year_tuition' else 'Annual tuition fee',
        comparison_period='full_time_course' if source.fee_basis=='full_time_course_fee' else 'first_year' if source.fee_basis=='first_year_tuition' else 'annual',
        fee_note=raw['fee_note'],duration_verified=True,published_duration=raw['published_duration'])

def saved_profile(request,course_id,academic_year):
    from app.scraping.uk.aston.awards import FACTS
    data=getattr(request,'session',{}).get('scholarship_eligibility',{})
    uni=data.get('UK007',{}) if isinstance(data,dict) else {}
    row=uni.get(str(course_id),{}) if isinstance(uni,dict) else {}
    answers=row.get('answers',{}) if isinstance(row,dict) and row.get('academic_year')==academic_year else {}
    return {k:answers[k] if isinstance(answers,dict) and type(answers.get(k)) is bool else None for k in FACTS}


def calculate(tuition,awards,profile,*,academic_year,study_year,allow_unverified_legacy=False):
    from app.scraping.uk.aston.awards import specs,make_rule,notices
    defined=specs(all_fee_manifest());permitted=[]
    source=getattr(tuition,'source_metadata',None)
    valid=study_year==1 and source is not None and fee_error(tuition,source) is None and source.academic_year==academic_year
    if valid:
        for award in awards:
            try:rule=award.calculation_rule
            except AttributeError:continue
            raw=rule.rules
            if not isinstance(raw,dict):continue
            spec=defined.get(raw.get('spec_key'))
            if not spec or tuition.course.course_id not in spec['course_ids']:continue
            content={k:v for k,v in raw.items() if k!='evidence_sha256'}
            saved_ids=raw.get('conditions',{}).get('course_id',[])
            if not isinstance(saved_ids,list) or not set(saved_ids).issubset(spec['course_ids']) or tuition.course.course_id not in saved_ids:continue
            scoped_spec={**spec,'course_ids':saved_ids}
            digest=raw.get('evidence_sha256','')
            if (rule.verified and rule.source_url==spec['source_url'] and content==make_rule(scoped_spec)
                and len(digest)==64 and all(ch in '0123456789abcdef' for ch in digest)):
                # The deadline closes new applications, not an existing confirmed
                # grant. Both Cadbury acceptance/applied facts are required above.
                confirmed=copy(award)
                confirmed.deadline=None
                permitted.append(confirmed)
    result=calculate_scholarships(tuition,permitted,profile,academic_year=academic_year,study_year=study_year,allow_unverified_legacy=False)
    result['eligibility_location']='scholarship_page';result['award_notices']=notices()
    result.setdefault('notes',[]).append('Only confirmed saved Aston facts determine a deduction. Community and Cadbury awards are not added together. Scholarship funds do not pay the programme deposit. Visit the scholarship page for eligibility and confirmation.')
    result['notes'].append('Deposit amounts remain offer-letter specific. Confirm the amount and deadline in your Aston offer.')
    if tuition.course.course_id in {'C_UK007_17','C_UK007_24','C_UK007_31','C_UK007_32','C_UK007_33','C_UK007_50'}:
        result['notes'].append('This programme is excluded from the reviewed Community and Cadbury scholarships. Other awards, including MBA-specific awards, require separate review.')
    else:
        for notice in result['award_notices']:result['notes'].append(notice['name']+': '+notice['label']+'. '+notice['note'])
    if tuition.course.course_id in {'C_UK007_24','C_UK007_50'}:
        result['notes'].append('MBA Gold/Silver award amounts are advertised for 2026/27, but linked terms cover 2025/26. Exact-intake scholarship confirmation is pending; no MBA award is deducted. Visit the scholarship page with your award letter.')
    if tuition.course.course_id=='C_AST007_01':
        result['notes'].append('Top-up: single GBP 3,000 payment only, subject to Aston confirming the exact entry route and all conditions. No later-year or Cadbury deduction is inferred.')
    if tuition.course.course_id=='C_UK007_26':
        result['notes'].append('MPH: September Community award may apply; Cadbury explicitly excludes MPH. Full-time route only.')
    result['label']='Tuition after confirmed Aston award' if Decimal(result['discount'])>0 else 'Tuition before an unconfirmed award'
    return result


def deposit(*,tuition,academic_year,profile):
    from app.models import DepositPolicy,TuitionSource
    from app.scraping.uk.aston.awards import FUNDING,deposit_rule
    result=dict(available=False,amount=None,currency='GBP',academic_year=academic_year,
        source_url=None,policy_checked_at=None,rule=None,is_estimate=False,included_in_tuition=True,
        label='Confirm deposit in your Aston offer',note='Deposit amounts remain offer-letter specific. This course/intake needs deposit reconciliation; no stored numeric deposit is assumed.')
    source=TuitionSource.objects.filter(tuition=tuition).first()
    if source is None or fee_error(tuition,source) or source.academic_year!=academic_year:return result
    policies=list(DepositPolicy.objects.filter(university_id='UK007',academic_year=academic_year,currency='GBP',verified=True))
    if len(policies)!=1 or policies[0].rules!=deposit_rule(academic_year,all_fee_manifest()) or policies[0].source_url!=FUNDING:return result
    p=policies[0]
    mba=p.rules.get('mba_acceptance_deposits',{}).get(tuition.course.course_id)
    if mba:
        note='Published MBA acceptance deposit: GBP 5,000, included in tuition and credited upon enrolment. Deposit amounts remain offer-letter specific; confirm the amount, deadline and any waiver in your exact offer. This is not an additional tuition charge or a scholarship deduction.'
        confirmed=profile.get('aston_mba_deposit_required_confirmed') is True and profile.get('aston_no_deposit_waiver_confirmed') is True
        return {**result,'available':confirmed,'amount':'5000.00' if confirmed else None,
            'published_amount':'5000.00','source_url':mba['source_url'],
            'policy_checked_at':p.checked_at.isoformat() if p.checked_at else None,
            'rule':'mba_offer_acceptance','payment_stage':'offer_acceptance',
            'label':'MBA acceptance deposit' if confirmed else 'Published MBA deposit — confirm your offer',
            'note':note}
    return {**result,'source_url':p.source_url,'policy_checked_at':p.checked_at.isoformat() if p.checked_at else None,
        'rule':'offer_letter','note':p.rules['note']}

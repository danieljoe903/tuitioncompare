"""AUP source-backed annual fees, conditional Alumni deduction and pre-CAS allocation."""
from decimal import Decimal,InvalidOperation
from app.scholarship_calculation import calculate_scholarships
from app.models import DepositPolicy
from app.scraping.uk.aup.award_status import competitive_award_notices
OWNER='aup_reviewed23_v1'
AWARD_OWNER='aup_alumni2027_v1'
YEAR='2027/28'
SOURCE='https://www.aup.ac.uk/international/finance'
ALUMNI_SOURCE='https://www.aup.ac.uk/study/postgraduate/fees-finance-funding'
UG_IDS={f'C_UK006_{n:02d}' for n in range(1,17)}
PG_IDS={f'C_UK006_{n:02d}' for n in [17,19,20,21,22,23,25]}
IDS=UG_IDS|PG_IDS
FACTS={
 'aup_fee_status_confirmed':'Has AUP confirmed your international fee status?',
 'aup_offer_confirmed':'Do you hold the offer for this exact September 2027 course and full-time route?',
 'aup_alumni_qualifying_award':'Have you completed an AUP undergraduate, pre-degree or Foundation Diploma qualification covered by the published Alumni Progression Award?',
 'aup_alumni_discount_confirmed_for_intake':'Has AUP confirmed that its 15% Alumni Progression Award applies to your exact September 2027 MA intake and compared tuition period?',
 'aup_no_other_award':'Are you using this Alumni discount alone, with no other scholarship, bursary or fee waiver combined?',
}
VISA='requires_student_visa'

def make_rule():
    conditions={k:[True] for k in FACTS};conditions.update(course_id=sorted(PG_IDS),fee_status=['INTL'])
    return dict(importer=AWARD_OWNER,benefit='tuition',kind='percentage',value='15',currency='GBP',academic_year=YEAR,
        study_years=[1],conditions=conditions,award_status='conditional',stacking='exclusive',compatible_ids=[],
        percentage_basis='original_tuition',fee_basis_confirmed=True,fee_basis='annual_tuition',
        scope_note='Seven reviewed full-time September 2027 MAs. AUP must confirm personal qualification and applicability to the exact intake/period; no inferred combination with other awards. Historical application-page text names 2024/25.',
        eligibility_questions=[dict(key=k,type='boolean',label=v) for k,v in FACTS.items()],questions_location='scholarship_page')

def policy_rules():
    return dict(importer=OWNER,kind='fixed_pre_cas',academic_year=YEAR,currency='GBP',amount='2000.00',course_ids=sorted(IDS),
        fee_status='INTL',requires_student_visa=True,payment_stage='before_cas',included_in_tuition=True,
        refundable=False,balance_due='full_remaining_balance_at_enrolment',note='Deposit is allocated towards tuition. AUP says no partial payments/payment plans for the remaining balance at enrolment. Confirm final payment instructions with AUP.')

def saved_profile(request,course_id,academic_year):
    data=getattr(request,'session',{}).get('scholarship_eligibility',{})
    uni=data.get('UK006',{}) if isinstance(data,dict) else {}
    row=uni.get(str(course_id),{}) if isinstance(uni,dict) else {}
    answers=row.get('answers',{}) if isinstance(row,dict) and row.get('academic_year')==academic_year else {}
    return {k:answers[k] if isinstance(answers,dict) and type(answers.get(k)) is bool else None for k in [*FACTS,VISA]}

def fee_error(tuition,source):
    c=tuition.course;r=source.raw_fees if source and isinstance(source.raw_fees,dict) else {}
    if c.course_id not in IDS or r.get('importer')!=OWNER or r.get('verified') is not True:
        return 'AUP exact course route, intake and international tuition remain unverified. No legacy fee or scholarship deduction is displayed.'
    expected=Decimal('17500.00') if c.course_id in {'C_UK006_15','C_UK006_16'} else Decimal('18000.00')
    try:amount=Decimal(str(tuition.tuition_fee))
    except (ValueError,TypeError,InvalidOperation):return 'Invalid AUP tuition amount.'
    if (str(c.university_id)!='UK006' or not c.is_active or c.start_month!='September' or c.start_year!=2027
        or c.level!=('PG' if c.course_id in PG_IDS else 'UG') or c.course_url!=r.get('source_course_url')
        or c.duration!=r.get('duration') or c.location!=r.get('location')
        or tuition.residency!='INTL' or tuition.currency!='GBP' or not amount.is_finite() or amount!=expected
        or source.academic_year!=YEAR or r.get('academic_year')!=YEAR or source.fee_basis!='annual_tuition'
        or not source.source_url or r.get('annual_tuition')!=str(expected) or r.get('currency')!='GBP'
        or r.get('source_fee_category')!='International'):
        return 'AUP saved fee, mode, duration or intake does not match the reviewed annual route.'
    return None

def fee_metadata(source,raw):
    return dict(fee_label='Annual tuition fee',fee_basis='annual_tuition',academic_year=YEAR,comparison_period='annual',
        fee_note=raw['fee_note'],duration_verified=True,course_availability_status='subject_to_validation' if raw.get('subject_to_validation') else 'advertised',
        award_applicability_note='Competitive award cohort terms require confirmation. Alumni deduction is available only to the scoped MAs with saved AUP confirmation.')

def calculate(tuition,awards,profile,*,academic_year,study_year,allow_unverified_legacy=False):
    permitted=[]
    try: source=tuition.source_metadata
    except AttributeError: source=None
    fee_valid=academic_year==YEAR and study_year==1 and fee_error(tuition,source) is None
    for award in awards:
        try:r=award.calculation_rule
        except AttributeError:continue
        if (fee_valid and r.verified and r.source_url==ALUMNI_SOURCE and r.rules==make_rule()
            and tuition.course.course_id in PG_IDS):permitted.append(award)
    result=calculate_scholarships(tuition,permitted,profile,academic_year=academic_year,study_year=study_year,allow_unverified_legacy=False)
    result.update(eligibility_location='scholarship_page',comparison_period='annual')
    result['award_notices'] = competitive_award_notices()
    for notice in result['award_notices']:
        result.setdefault('notes', []).append(notice['name'] + ': ' + notice['label'] + '. ' + notice['message'])
    result.setdefault('notes',[]).append('Competitive AUP scholarships have an unconfirmed 2027 application cycle; no maximum or UCA-linked legacy award is deducted. Save applicable eligibility and university confirmation on the scholarship page.')
    if Decimal(result['discount'])>0:result['label']='Tuition after confirmed Alumni Progression Award'
    else:result['label']='Tuition before an unconfirmed award'
    return result

def deposit(*,tuition,academic_year,profile):
    result=dict(available=False,amount=None,currency='GBP',academic_year=academic_year,source_url=SOURCE,
        policy_checked_at=None,rule='fixed_pre_cas',is_estimate=False,label='Confirm deposit eligibility',
        included_in_tuition=True,is_cas_deposit=True,missing_facts=[],note='If you need a Student visa, AUP publishes a GBP 2,000 deposit before CAS. It counts towards tuition; the remaining balance is due in full at enrolment. No scholarship is a confirmed deposit payment.')
    if academic_year!=YEAR or tuition.course.course_id not in IDS:return {**result,'note':'AUP deposit course/year scope requires confirmation.'}
    try:source=tuition.source_metadata
    except AttributeError:return result
    if fee_error(tuition,source):return {**result,'note':'Verify the course and tuition before applying a deposit policy.'}
    policies=list(DepositPolicy.objects.filter(university_id='UK006',academic_year=YEAR,currency='GBP',verified=True))
    if len(policies)!=1 or policies[0].rules!=policy_rules() or policies[0].source_url!=SOURCE:
        return {**result,'note':'No matching reviewed AUP deposit policy is saved.'}
    policy=policies[0];result['policy_checked_at']=policy.checked_at.isoformat() if policy.checked_at else None
    if profile.get(VISA) is None:return {**result,'missing_facts':[VISA]}
    if profile[VISA] is not True:return {**result,'label':'Pre-CAS policy not applicable','note':'This GBP 2,000 pre-CAS rule applies to applicants needing a Student visa. Confirm other payment arrangements with AUP; no zero deposit is assumed.'}
    return {**result,'available':True,'amount':'2000.00','label':'Deposit before CAS, included in tuition','note':result['note']+' AUP confirms final payment instructions; the deposit is stated as non-refundable.'}

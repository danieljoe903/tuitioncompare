"""AUB annual comparison using reviewed source and saved scholarship-page facts."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from app.scholarship_calculation import calculate_scholarships

OWNER='aub_campus_first30_v1'
AWARD_OWNER='aub_competitive_confirmed_amount_v1'
FACTS={
 'aub_fee_status_confirmed':'Has AUB assessed you as an overseas fee payer?',
 'aub_offer_confirmed':'Do you hold the AUB offer for this exact course and intake?',
 'aub_application_by_deadline':'Did you submit the scholarship application by the published deadline for your intake?',
 'aub_award_granted':'Has AUB granted you this international scholarship?',
 'aub_award_accepted':'Have you accepted the scholarship under AUB’s instructions?',
 'aub_award_for_compared_period':'Does the award letter make this amount applicable to the fee period being compared?',
 'aub_no_other_aub_award':'Have you confirmed that you are not combining it with another AUB financial award or bursary?',
}
QUESTIONS=[{'key':k,'type':'boolean','label':v} for k,v in FACTS.items()]+[
 {'key':'aub_confirmed_award_amount','type':'money','currency':'GBP','label':'What exact scholarship amount has AUB granted for this fee period? Use the award letter; do not enter the advertised maximum unless that is your award.'}]


def decimal_amount(value):
    try:
        n=Decimal(str(value))
        return n if n.is_finite() and n>0 and n==n.quantize(Decimal('.01')) else None
    except (InvalidOperation,ValueError,TypeError):return None


def fee_error(tuition,source):
    if source is None:return 'AUB international tuition is awaiting verification for this course and intake.'
    r=source.raw_fees
    if not isinstance(r,dict) or r.get('importer')!=OWNER or r.get('verified') is not True:
        return 'AUB international tuition is awaiting verification for this exact route and intake.'
    if (source.academic_year!='2027/28' or r.get('academic_year')!=source.academic_year
        or source.fee_basis!='annual_tuition' or not source.source_url
        or tuition.currency!='GBP' or r.get('currency')!='GBP' or r.get('source_fee_category')!='International'
        or r.get('study_mode')!='campus' or r.get('default_intake')!='2027-09'
        or not decimal_amount(tuition.tuition_fee)
        or decimal_amount(tuition.tuition_fee)!=decimal_amount(r.get('annual_tuition'))):
        return 'The AUB fee, route or academic-year evidence requires review.'
    return None


def fee_metadata(source,raw):
    return dict(fee_label='Annual tuition fee',academic_year=source.academic_year,fee_basis=source.fee_basis,
                fee_note=raw['fee_note'],comparison_period='annual',
                course_availability_status='subject_to_validation' if raw.get('subject_to_validation') else 'advertised',
                duration_verified=raw.get('duration_verified',False),duration_note=raw.get('duration_note',''))


def saved_profile(request,course_id,academic_year):
    """Contract for the scholarship page; no eligibility query parameters read.
    session['scholarship_eligibility']['UK005'][course_id] = {
      'academic_year':'2027/28', 'answers': { ...boolean facts..., actual amount... }}
    The scholarship page must validate/store these facts on its server side.
    """
    session=getattr(request,'session',{})
    data=session.get('scholarship_eligibility',{})
    if not isinstance(data,dict):return {}
    university=data.get('UK005',{})
    record=university.get(str(course_id),{}) if isinstance(university,dict) else {}
    if not isinstance(record,dict) or record.get('academic_year')!=academic_year:return {}
    answers=record.get('answers',{})
    if not isinstance(answers,dict):return {}
    profile={k:answers[k] for k in FACTS if type(answers.get(k)) is bool}
    profile['aub_confirmed_award_amount']=answers.get('aub_confirmed_award_amount')
    return profile


def calculate(tuition,awards,profile,*,academic_year,study_year,allow_unverified_legacy=False):
    prepared=[];actual=decimal_amount(profile.get('aub_confirmed_award_amount'))
    derived=dict(profile)
    derived['aub_award_amount_valid']=False
    for award in awards:
        try:rule=award.calculation_rule
        except AttributeError:continue
        r=rule.rules
        if (not rule.verified or not rule.source_url or not isinstance(r,dict)
            or r.get('importer')!=AWARD_OWNER or r.get('kind')!='confirmed_amount'):continue
        ceiling=decimal_amount(r.get('published_maximum'))
        conditions=r.get('conditions',{})
        if (r.get('academic_year')!=academic_year or str(tuition.course.course_id) not in conditions.get('course_id',[])):
            continue
        rules=deepcopy(r)
        if actual and ceiling and actual<=ceiling:
            rules.update(kind='fixed',value=str(actual))
            derived['aub_award_amount_valid']=True
        prepared.append(SimpleNamespace(pk=award.pk,name=award.name,deadline=award.deadline,
            calculation_rule=SimpleNamespace(verified=rule.verified,source_url=rule.source_url,rules=rules)))
    result=calculate_scholarships(tuition,prepared,derived,academic_year=academic_year,study_year=study_year,allow_unverified_legacy=False)
    if Decimal(result['discount'])==0:
        result['label']='Tuition before an unconfirmed scholarship'
        result.setdefault('notes',[]).append('Complete scholarship eligibility on the scholarship page and save the exact granted amount. The advertised maximum is not automatically deducted.')
    else:
        result.setdefault('notes',[]).append('Uses the saved AUB award amount for this course and period. AUB’s award letter confirms the award; no other AUB award is stacked.')
    result['eligibility_location']='scholarship_page'
    return result


def deposit(*,tuition,academic_year):
    return dict(available=False,amount=None,currency=tuition.currency,academic_year=academic_year,
                source_url='https://webdocs.aub.ac.uk/Fees%20Payment%20Policy.pdf',policy_checked_at=None,
                rule='cohort_confirmation_required',is_estimate=False,label='Confirm deposit with AUB',
                note='The collected payment policy is labelled 2026/27. Confirm 2027/28 acceptance and pre-CAS requirements with AUB; no prior-year numeric amount is carried forward.',missing_facts=[])

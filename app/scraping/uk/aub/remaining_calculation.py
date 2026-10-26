"""AUB reviewed remaining routes; preserve the original thirty-course adapter."""
from decimal import Decimal
from app.scraping.uk.aub import calculation as original

OWNER='aub_remaining13_v1'
ONLINE_IDS={'C_UK005_23','C_UK005_40','C_UK005_41','C_UK005_42'}
FOUNDATION_IDS={f'C_UK005_{n:02d}' for n in range(43,51)}
CAMPUS_ID='C_UK005_33'
AWARD_OWNER='aub_online_progression_v1'
PROGRESSION_FACT='aub_progression_qualifying_undergraduate'


def saved_profile(request,course_id,academic_year):
    result=original.saved_profile(request,course_id,academic_year)
    data=getattr(request,'session',{}).get('scholarship_eligibility',{})
    uni=data.get('UK005',{}) if isinstance(data,dict) else {}
    row=uni.get(str(course_id),{}) if isinstance(uni,dict) else {}
    answers=row.get('answers',{}) if isinstance(row,dict) and row.get('academic_year')==academic_year else {}
    if isinstance(answers,dict) and type(answers.get(PROGRESSION_FACT)) is bool:result[PROGRESSION_FACT]=answers[PROGRESSION_FACT]
    from app.scraping.uk.aub.early_payment import saved_facts
    result.update(saved_facts(answers))
    return result


def comparison_filter(raw,basis):
    return basis=='programme_total' and raw.get('importer')==OWNER and raw.get('route_kind')=='online' and raw.get('verified') is True


def fee_error(tuition,source,comparison_basis='annual'):
    if source is None or not isinstance(source.raw_fees,dict) or source.raw_fees.get('importer')!=OWNER:
        return original.fee_error(tuition,source)
    raw=source.raw_fees;c=tuition.course
    if (source.academic_year!='2027/28' or raw.get('academic_year')!='2027/28' or tuition.currency!='GBP'
        or raw.get('currency')!='GBP' or raw.get('source_fee_category')!='International'
        or not source.source_url or raw.get('default_intake')!='2027-09'
        or c.start_year!=2027 or c.start_month!='September' or c.course_url!=raw.get('source_course_url')):
        return 'AUB route, intake or fee-source evidence requires review.'
    kind=raw.get('route_kind');cid=c.course_id
    if kind=='foundation' and cid in FOUNDATION_IDS and not (raw.get('foundation_fee_confirmation',{}).get('importer')=='aub_foundation_fees_v1' and raw.get('verified') is True):
        return 'The exact September 2027 foundation route is reviewed. Published international tuition is provisionally GBP 19,950 per year; final confirmation is pending. No final verified fee or scholarship deduction is shown.'
    key='programme_total' if kind=='online' else 'annual_tuition'
    expected='9975.00' if kind=='online' else '19950.00'
    if (raw.get('verified') is not True or original.decimal_amount(raw.get(key))!=Decimal(expected)
        or original.decimal_amount(tuition.tuition_fee)!=Decimal(expected)
        or source.fee_basis!=('total_programme_fee' if kind=='online' else 'annual_tuition')):
        return 'The saved AUB fee amount or pricing basis requires review.'
    if kind=='online' and cid in ONLINE_IDS:
        if comparison_basis!='programme_total':return 'This two-year online course publishes GBP 9,975 for the full programme, not an annual fee. The programme total is available separately; applicable GST/VAT may be additional.'
        if c.location!='Online' or c.duration!='2 Years':return 'The saved online mode or duration requires review.'
        return None
    if kind=='foundation' and cid in FOUNDATION_IDS and comparison_basis=='annual' and raw.get('foundation_fee_confirmation',{}).get('importer')=='aub_foundation_fees_v1':return None
    if kind=='campus' and cid==CAMPUS_ID and comparison_basis=='annual':return None
    return 'The AUB route or requested fee basis is outside the reviewed scope.'


def review_summary(source,raw):
    if raw.get('importer')!=OWNER:return {}
    kind=raw.get('route_kind');amount=raw.get('published_provisional_fee') if kind=='foundation' else raw.get('programme_total') if kind=='online' else raw.get('annual_tuition')
    return dict(published_tuition={'amount':amount,'currency':'GBP','academic_year':'2027/28','fee_basis':source.fee_basis,'provisional':kind=='foundation' and raw.get('verified') is not True,'source_url':source.source_url},reviewed_academic_year='2027/28',source_evidence_kind=raw.get('route_evidence_kind'),award_applicability_note=raw.get('scholarship_review',{}).get('reason',''),payment_note=raw.get('deposit_review',{}).get('message',''))


def fee_metadata(source,raw):
    if raw.get('importer')!=OWNER:return original.fee_metadata(source,raw)
    online=raw.get('route_kind')=='online'
    return dict(fee_label='Total programme tuition' if online else 'Annual tuition fee',academic_year='2027/28',fee_basis=source.fee_basis,comparison_period='total_programme' if online else 'annual',fee_note=raw['fee_note'],duration_verified=raw.get('duration_verified',False),duration_note=raw.get('duration_note',''),course_availability_status='validation_status_unconfirmed' if raw.get('route_kind')=='foundation' and raw.get('subject_to_validation') is None else 'advertised',validation_status_note=raw.get('validation_status_note',''),source_evidence_kind=raw.get('route_evidence_kind'))


def _calculate_without_waiver(tuition,awards,profile,*,academic_year,study_year,allow_unverified_legacy=False):
    cid=tuition.course.course_id
    if cid in ONLINE_IDS:
        permitted=[]
        for award in awards:
            try:rule=award.calculation_rule
            except AttributeError:continue
            r=rule.rules
            if (rule.verified and rule.source_url and isinstance(r,dict) and r.get('importer')==AWARD_OWNER
                and r.get('kind')=='fixed' and r.get('value')=='1995.00' and r.get('academic_year')=='2027/28'
                and str(cid) in r.get('conditions',{}).get('course_id',[])):
                permitted.append(award)
        from app.scholarship_calculation import calculate_scholarships
        result=calculate_scholarships(tuition,permitted,profile,academic_year=academic_year,study_year=study_year,allow_unverified_legacy=False)
        result.update(comparison_period='total_programme',eligibility_location='scholarship_page')
        result.setdefault('notes',[]).append('The online progression bursary is GBP 1,995 for this programme total, conditional on AUB undergraduate progression, exact intake, overseas-fee status, timely application and confirmed award acceptance. No other AUB award is stacked.')
        return result
    if cid in FOUNDATION_IDS:
        result=original.calculate(tuition,[],{},academic_year=academic_year,study_year=study_year,allow_unverified_legacy=False)
        result.update(label='Tuition before unconfirmed awards',notes=['Integrated foundation Year 0 applicability is unconfirmed. Foundation Diploma funding is a separate provision; no deduction applied.'],comparison_period='annual')
        return result
    return original.calculate(tuition,awards,profile,academic_year=academic_year,study_year=study_year,allow_unverified_legacy=False)


def deposit(*,tuition,academic_year):
    try:source=tuition.source_metadata
    except AttributeError:source=None
    raw=source.raw_fees if source and isinstance(source.raw_fees,dict) else {}
    if (tuition.course.course_id in ONLINE_IDS and raw.get('importer')==OWNER and raw.get('verified') is True
        and academic_year=='2027/28' and raw.get('academic_year')==academic_year and raw.get('acceptance_payment')=='500.00'):
        return dict(available=True,amount='500.00',currency='GBP',academic_year=academic_year,source_url=source.source_url,policy_checked_at=source.checked_at.isoformat() if source.checked_at else None,rule='online_acceptance_payment',is_estimate=False,label='Acceptance payment included in tuition',payment_stage='acceptance',included_in_tuition=True,is_cas_deposit=False,missing_facts=[],note='AUB publishes a non-refundable GBP 500 acceptance payment, allocated to the first tuition payment after study starts. It is included in the GBP 9,975 total; do not add it again. This is not a campus pre-CAS deposit.')
    return original.deposit(tuition=tuition,academic_year=academic_year)


def calculate(tuition,awards,profile,*,academic_year,study_year,allow_unverified_legacy=False):
    awards=list(awards)
    result=_calculate_without_waiver(tuition,awards,profile,academic_year=academic_year,study_year=study_year,allow_unverified_legacy=False)
    from app.scraping.uk.aub.early_payment import apply_waiver
    return apply_waiver(tuition,awards,profile,result,academic_year=academic_year,study_year=study_year,fee_error=fee_error)

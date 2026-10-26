"""2027/28 annual campus waiver; saved scholarship-page facts only."""
from datetime import date
from decimal import Decimal

OWNER = 'aub_early_payment_2027_v1'
SOURCE = 'https://aub.ac.uk/international/international-scholarships/international-early-payment-fee-waiver'
DEADLINE = date(2027, 8, 1)
FACTS = {
    'aub_fee_status_confirmed': 'Has AUB assessed you as an overseas fee payer?',
    'aub_offer_accepted': 'Have you accepted the offer for this exact September 2027 course?',
    'aub_full_annual_fee_paid': 'Has AUB confirmed receipt of the full annual tuition payment?',
    'aub_no_other_award_received': 'Have you confirmed that no other AUB scholarship, bursary or financial award has been awarded to you for this period?',
}
DATE_KEY = 'aub_full_annual_payment_date'
UG_IDS = {f'C_UK005_{n:02d}' for n in [1,2,3,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,26,29,*range(43,51)]}
PG_IDS = {f'C_UK005_{n:02d}' for n in [21,24,25,33,34,35,36,37,38,39]}
# MArch excluded: the postgraduate deadline explicitly names MA courses.
SCOPES = {'UG': UG_IDS, 'PG': PG_IDS}


def saved_facts(answers):
    if not isinstance(answers, dict):
        return {}
    result = {k: answers[k] for k in FACTS if type(answers.get(k)) is bool}
    if isinstance(answers.get(DATE_KEY), str):
        result[DATE_KEY] = answers[DATE_KEY]
    return result


def make_rule(level):
    conditions = {key: [True] for key in FACTS}
    conditions.update(course_id=sorted(SCOPES[level]), fee_status=['INTL'], aub_early_payment_date_valid=[True])
    return dict(importer=OWNER, benefit='tuition', kind='fixed', value='1500.00', currency='GBP',
        academic_year='2027/28', study_years=[1], conditions=conditions, award_status='conditional',
        stacking='exclusive', compatible_ids=[], percentage_basis='original_tuition',
        fee_basis_confirmed=True, fee_basis='annual_tuition', payment_deadline=DEADLINE.isoformat(),
        scope_note='Reviewed September 2027 annual campus fees only. Includes integrated foundation Year 0; excludes online totals, MArch and unresolved routes. No stacking. No award-letter amount required for this fixed payment waiver.',
        eligibility_questions=[{'key':k,'type':'boolean','label':v} for k,v in FACTS.items()] +
            [{'key':DATE_KEY,'type':'date','label':'On what date did AUB receive the full annual tuition payment? Use the confirmed receipt date.'}],
        questions_location='scholarship_page')


def apply_waiver(tuition, awards, profile, previous, *, academic_year, study_year, fee_error):
    c = tuition.course
    level = 'UG' if c.course_id in UG_IDS else 'PG' if c.course_id in PG_IDS else None
    if level is None:
        return previous
    previous.setdefault('notes', []).append('A separate GBP 1,500 early-payment waiver may apply if AUB receives the full annual tuition by 1 August 2027 and no other AUB award has been awarded. Save confirmed payment facts on the scholarship page; it is not stacked with a scholarship.')
    try:
        source = tuition.source_metadata
        received = date.fromisoformat(profile.get(DATE_KEY, ''))
    except (AttributeError, TypeError, ValueError):
        return previous
    if (academic_year != '2027/28' or study_year != 1 or c.start_year != 2027
        or c.start_month != 'September' or not c.is_active or c.level != level
        or tuition.residency != 'INTL' or tuition.currency != 'GBP'
        or fee_error(tuition, source, 'annual') is not None
        or Decimal(str(tuition.tuition_fee)) != Decimal('19950.00')
        or not date(2026,1,1) <= received <= min(DEADLINE, date.today())
        or profile.get('aub_award_granted') is True
        or Decimal(previous['discount']) != 0
        or any(profile.get(k) is not True for k in FACTS)):
        return previous
    expected = make_rule(level)
    permitted = []
    for award in awards:
        try:
            rule = award.calculation_rule
        except AttributeError:
            continue
        if rule.verified and rule.source_url == SOURCE and rule.rules == expected:
            permitted.append(award)
    derived = dict(profile, aub_early_payment_date_valid=True)
    from app.scholarship_calculation import calculate_scholarships
    result = calculate_scholarships(tuition, permitted, derived, academic_year=academic_year,
        study_year=study_year, allow_unverified_legacy=False)
    if Decimal(result['discount']) != Decimal('1500.00'):
        return previous
    result.update(label='Tuition after confirmed early-payment waiver', comparison_period='annual',
        eligibility_location='scholarship_page')
    result.setdefault('notes', []).append('Fixed GBP 1,500 early-payment waiver using saved confirmed receipt and eligibility facts. No other AUB award is included. Integrated Year 0 competitive scholarship applicability remains separate. Campus deposit requirements remain unconfirmed for 2027/28.')
    return result

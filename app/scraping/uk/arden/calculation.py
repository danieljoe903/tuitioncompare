"""Read-only Arden calculation inputs and individually advised deposits."""
from datetime import datetime
from zoneinfo import ZoneInfo
from app.models import DepositPolicy

IMPORTER = 'arden_campus_pg_v1'
DEADLINES = {'2026-11': '2026-10-29', '2027-02': '2027-01-25', '2027-05': '2027-04-12'}
FACTS = {
    'requires_student_visa': 'Do you require a visa to study this programme in the UK?',
    'arden_uk_blended_offer': 'Has Arden offered you this solely Arden-awarded UK blended-learning programme for the selected intake?',
    'arden_partner_route': 'Are you enrolling through a franchise or partnership programme (rather than directly or through an approved recruitment agent)?',
    'arden_prior_credits_under_40': 'Are you carrying fewer than 40 credits from previous learning, including previous Arden study?',
    'arden_account_in_good_standing': 'Is your Arden student account free of arrears?',
    'arden_not_blocked_or_expelled': 'Are you free of any blocked or expelled student status?',
    'arden_no_staff_agent_connection': 'Have you confirmed that none of the staff, staff-family, agent or sub-agent exclusions in the offer terms apply to you?',
    'arden_application_accurate': 'Is your application and supporting information accurate and free of misrepresentation?',
    'arden_not_sanctioned': 'Has your eligibility under the offer terms concerning sanctioned persons been confirmed?',
    'arden_student_visa_sponsored': 'Will Arden sponsor your Student visa for this programme?',
    'arden_programme_at_least_120_credits': 'Is your eligible postgraduate programme at least 120 credits?',
}


def boolean(value):
    if value is None:
        return None
    value = str(value).strip().lower()
    return True if value in {'yes', 'true', '1'} else False if value in {'no', 'false', '0'} else None


def selection(request, raw):
    campus = request.GET.get('arden_campus', '').strip()
    campus = {'manchester': 'Manchester', 'london stratford': 'London Stratford',
              'london_stratford': 'London Stratford'}.get(campus.casefold(), campus)
    intake = request.GET.get('arden_intake', '').strip() or raw.get('default_intake')
    return campus or None, intake


def selection_error(request, raw):
    if raw.get('importer') != IMPORTER:
        return 'An applicable Arden route and fee have not been reviewed.'
    campus, intake = selection(request, raw)
    routes = raw.get('campus_intakes', {})
    if campus is not None and campus not in routes:
        return 'Select a reviewed Arden campus: Manchester or London Stratford.'
    available = routes.get(campus, []) if campus else [x for dates in routes.values() for x in dates]
    if intake not in available:
        return 'This Arden campus/intake combination has not been verified. Select an advertised option.'
    return None


def by_deadline(value, intake):
    if not value or intake not in DEADLINES:
        return None
    try:
        actual = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
        if actual.tzinfo is None or actual.utcoffset() is None:
            return None
        cutoff = datetime.fromisoformat(DEADLINES[intake] + 'T17:30:00').replace(tzinfo=ZoneInfo('Europe/London'))
        return actual <= cutoff
    except (ValueError, TypeError):
        return None


def build_profile(request, raw):
    campus, intake = selection(request, raw)
    profile = {key: boolean(request.GET.get(key)) for key in FACTS}
    profile.update(arden_campus=campus, arden_intake=intake)
    profile['arden_unconditional_offer_on_time'] = by_deadline(request.GET.get('arden_unconditional_offer_at'), intake)
    profile['arden_deposit_paid_on_time'] = by_deadline(request.GET.get('arden_deposit_paid_at'), intake)
    return profile


def eligibility_questions():
    questions = [
        {'key': 'arden_campus', 'label': 'Which Arden campus will you attend?', 'type': 'select',
         'options': [{'value': x, 'label': x} for x in ['Manchester', 'London Stratford']]},
        {'key': 'arden_intake', 'label': 'Which advertised intake will you join? Use YYYY-MM.', 'type': 'text'},
    ]
    questions.extend({'key': key, 'label': label, 'type': 'boolean'} for key, label in FACTS.items())
    questions.extend([
        {'key': 'arden_unconditional_offer_at', 'label': 'When did Arden issue your unconditional offer? Supply an ISO date/time with UTC offset.', 'type': 'text'},
        {'key': 'arden_deposit_paid_at', 'label': 'When was your required deposit paid? Supply an ISO date/time with UTC offset.', 'type': 'text'},
    ])
    return questions


def calculate_deposit(*, tuition, academic_year):
    result = dict(available=False, amount=None, currency='GBP', academic_year=academic_year,
                  source_url=None, policy_checked_at=None, rule=None, is_estimate=False,
                  label='Confirm deposit with Arden', missing_facts=['individual_deposit_amount'],
                  note='Arden advises the deposit amount for your chosen course during application.')
    policy = DepositPolicy.objects.filter(university_id='UK004', academic_year=academic_year,
                                          currency='GBP', verified=True).first()
    if policy is None or not isinstance(policy.rules, dict):
        return {**result, 'note': 'No verified Arden deposit policy is saved for this period.'}
    if policy.rules.get('kind') != 'application_advised' or str(tuition.course_id) not in policy.rules.get('course_ids', []):
        return {**result, 'note': 'Deposit applicability requires review for this course.'}
    return {**result, 'source_url': policy.source_url, 'policy_checked_at': policy.checked_at.isoformat(),
            'rule': 'application_advised', 'note': policy.rules['note']}

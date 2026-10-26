"""Extend the tested Arden campus adapter with explicit programme-total routes."""
from . import calculation as campus

ONLINE_IMPORTER = 'arden_online_pg_v2'
TOPUP_IMPORTER = 'arden_campus_topup_v2'
ONLINE_FACTS = {
    'arden_online_offer_confirmed': 'Has Arden offered you this online programme for the selected intake?',
    'arden_not_uk_student': 'Has Arden confirmed that you are not classified as a UK student under the Global Tiers terms?',
    'arden_country_evidence_accepted': 'Has Arden accepted your nationality/residence evidence for Global Tiers eligibility?',
    'arden_partner_route': campus.FACTS['arden_partner_route'],
    'arden_prior_credits_under_40': campus.FACTS['arden_prior_credits_under_40'],
    'arden_account_in_good_standing': campus.FACTS['arden_account_in_good_standing'],
    'arden_not_blocked_or_expelled': campus.FACTS['arden_not_blocked_or_expelled'],
    'arden_no_staff_agent_connection': campus.FACTS['arden_no_staff_agent_connection'],
    'arden_application_accurate': campus.FACTS['arden_application_accurate'],
    'arden_not_sanctioned': campus.FACTS['arden_not_sanctioned'],
}


def online(raw):
    return raw.get('importer') == ONLINE_IMPORTER


def selection(request, raw):
    if online(raw):
        return 'Online', request.GET.get('arden_intake', '').strip() or raw.get('default_intake')
    return campus.selection(request, raw)


def selection_error(request, raw):
    if online(raw):
        requested = request.GET.get('arden_campus', '').strip()
        if requested and requested.casefold() != 'online':
            return 'This reviewed route is online. UK campus offers do not apply.'
        if request.GET.get('comparison_basis', 'annual').strip() != 'programme_total':
            return ('The published quote is a total programme fee, not annual tuition. '
                    'Use comparison_basis=programme_total to compare reviewed programme totals separately.')
        if request.GET.get('residence', '').strip().upper() not in raw.get('fee_country_scope', []):
            return 'The collected online quote currently covers Nigeria and Ghana; other fee countries require verification.'
        _, intake = selection(request, raw)
        if intake not in raw.get('online_intakes', []):
            return 'This online intake has not been reviewed.'
        return None
    if raw.get('importer') == TOPUP_IMPORTER:
        raw = {**raw, 'importer': campus.IMPORTER}
    return campus.selection_error(request, raw)


def build_profile(request, raw):
    if not online(raw):
        return campus.build_profile(request, raw)
    _, intake = selection(request, raw)
    profile = {key: campus.boolean(request.GET.get(key)) for key in ONLINE_FACTS}
    evidence_countries = [request.GET.get(key, '').strip().upper() for key in
                          ['arden_application_residence', 'arden_application_nationality']]
    profile.update(arden_campus='Online', arden_intake=intake,
                   arden_online_tier2_country_confirmed=(True if any(c in {'NG', 'GH'} for c in evidence_countries) else None),
                   arden_online_gross_quote=raw.get('total_programme_fee'))
    return profile


def eligibility_questions(request=None, raw=None):
    if not raw or not online(raw):
        return campus.eligibility_questions()
    questions = [{'key': 'arden_intake', 'label': 'Which reviewed online intake will you join? Use YYYY-MM.', 'type': 'text'}]
    questions.extend({'key': key, 'label': label, 'type': 'boolean'} for key, label in ONLINE_FACTS.items())
    questions.extend([
        {'key': 'arden_application_residence', 'label': 'Country of residence at application: use a two-letter country code. This batch verifies NG/GH eligibility only.', 'type': 'text'},
        {'key': 'arden_application_nationality', 'label': 'Nationality at application: use a two-letter country code. This batch verifies NG/GH eligibility only.', 'type': 'text'},
    ])
    return questions


def calculate_deposit(*, tuition, academic_year):
    try:
        raw = tuition.source_metadata.raw_fees
    except AttributeError:
        raw = {}
    if online(raw):
        return dict(available=False, amount=None, currency=tuition.currency, academic_year=academic_year,
                    source_url=raw.get('course_source_url'), policy_checked_at=None,
                    rule='campus_policy_not_applicable', is_estimate=False,
                    label='Campus pre-CAS deposit policy does not apply to online study',
                    note='Confirm any online payment or enrolment requirements with Arden. No online numeric deposit has been verified.',
                    missing_facts=[])
    return campus.calculate_deposit(tuition=tuition, academic_year=academic_year)

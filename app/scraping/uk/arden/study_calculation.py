"""Reviewed Arden undergraduate fees: level, programme-total and campus bases."""
from decimal import Decimal, InvalidOperation
from . import route_calculation as previous

LEVELS_IMPORTER = 'arden_online_ug_levels_v3'
ONLINE_TOPUP_IMPORTER = 'arden_online_ug_topup_v3'
CAMPUS_IMPORTER = 'arden_campus_ug_topup_v3'
UNAVAILABLE_IMPORTER = 'arden_unpublished_ug_v3'
ONLINE_IMPORTERS = {LEVELS_IMPORTER, ONLINE_TOPUP_IMPORTER}


def verification_error(tuition, source):
    """Validate the stored per-level amount before calculating any alternate basis."""
    raw = source.raw_fees
    try:
        levels = raw['fees_by_level']
        if set(levels) != {'4', '5', '6'}:
            return 'The complete Levels 4–6 fee schedule requires review.'
        amounts = [Decimal(str(levels[key])) for key in ('4', '5', '6')]
        if any(not a.is_finite() or a <= 0 for a in amounts):
            return 'A published level fee is invalid.'
        if (source.fee_basis != 'per_level' or raw.get('verified') is not True
                or not source.source_url or raw.get('academic_year') != source.academic_year
                or source.academic_year != '2026/27' or raw.get('currency') != tuition.currency
                or tuition.currency != 'GBP' or raw.get('source_fee_category') != 'International'
                or raw.get('stored_fee_level') != '4'
                or Decimal(str(tuition.tuition_fee)) != amounts[0]):
            return 'Stored per-level tuition does not match the reviewed source.'
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return 'The saved level-fee evidence requires review.'
    return None


def filter_matches(raw, basis):
    if basis == 'per_level':
        return raw.get('importer') == LEVELS_IMPORTER
    if basis == 'programme_total':
        return raw.get('importer') in {LEVELS_IMPORTER, ONLINE_TOPUP_IMPORTER, previous.ONLINE_IMPORTER}
    return True


def selection(request, raw):
    if raw.get('importer') in ONLINE_IMPORTERS:
        return 'Online', request.GET.get('arden_intake', '').strip() or raw.get('default_intake')
    return previous.selection(request, raw)


def selection_error(request, raw):
    importer = raw.get('importer')
    if importer == LEVELS_IMPORTER:
        basis = request.GET.get('comparison_basis', 'annual').strip()
        if basis not in {'per_level', 'programme_total'}:
            return 'This online fee is per level, not annual. Select comparison_basis=per_level or programme_total.'
        if basis == 'per_level' and request.GET.get('arden_level', '').strip() not in {'4', '5', '6'}:
            return 'Select arden_level=4, 5 or 6 for a per-level comparison.'
        normalized = {**raw, 'importer': previous.ONLINE_IMPORTER}
        # Reuse the tested online campus/country/intake guards with the correct basis.
        class Request:
            GET = {**request.GET.dict(), 'comparison_basis': 'programme_total'} if hasattr(request.GET, 'dict') else {**request.GET, 'comparison_basis': 'programme_total'}
        return previous.selection_error(Request(), normalized)
    if importer == ONLINE_TOPUP_IMPORTER:
        return previous.selection_error(request, {**raw, 'importer': previous.ONLINE_IMPORTER})
    if importer == CAMPUS_IMPORTER:
        return previous.selection_error(request, {**raw, 'importer': previous.TOPUP_IMPORTER})
    return previous.selection_error(request, raw)


def comparison_amount(request, raw, tuition):
    if raw.get('importer') == LEVELS_IMPORTER:
        if request.GET.get('comparison_basis', 'annual').strip() == 'programme_total':
            return sum((Decimal(raw['fees_by_level'][key]) for key in ('4', '5', '6')), Decimal('0'))
        return Decimal(raw['fees_by_level'][request.GET.get('arden_level', '').strip()])
    return Decimal(str(tuition.tuition_fee))


def fee_metadata(request, source, raw):
    if raw.get('importer') == LEVELS_IMPORTER:
        total = request.GET.get('comparison_basis', 'annual').strip() == 'programme_total'
        return dict(
            fee_basis='derived_programme_total' if total else 'per_level',
            published_fee_basis='per_level',
            fee_label='Derived programme tuition (Levels 4–6)' if total else 'Tuition for Level ' + request.GET.get('arden_level', '').strip(),
            comparison_period='total_programme' if total else 'per_level',
            selected_level=None if total else request.GET.get('arden_level', '').strip(),
            fees_by_level=raw['fees_by_level'], programme_total_is_derived=total,
            fee_note=(raw.get('fee_note', '') + (' Programme total is the sum of the three published level fees; it is not a separately published annual or total quote and does not guarantee future fee protection.' if total else ' Programme-wide Global Tiers awards are not divided across levels without a verified allocation rule.')),
        )
    total = source.fee_basis == 'total_programme_fee'
    return dict(fee_basis=source.fee_basis, fee_label='Total programme fee' if total else 'Annual tuition fee',
                comparison_period='total_programme' if total else 'annual', fee_note=raw.get('fee_note', ''))


def build_profile(request, raw):
    importer = raw.get('importer')
    if importer in ONLINE_IMPORTERS:
        copy = {**raw, 'importer': previous.ONLINE_IMPORTER}
        if importer == LEVELS_IMPORTER:
            copy['total_programme_fee'] = str(comparison_amount(request, raw, None).quantize(Decimal('.01')))
        profile = previous.build_profile(request, copy)
        profile['arden_comparison_period'] = ('per_level' if request.GET.get('comparison_basis') == 'per_level' else 'total_programme')
        return profile
    return previous.build_profile(request, raw)


def eligibility_questions(request=None, raw=None):
    if raw and raw.get('importer') in ONLINE_IMPORTERS:
        questions = previous.eligibility_questions(request, {**raw, 'importer': previous.ONLINE_IMPORTER})
        if raw.get('importer') == LEVELS_IMPORTER:
            questions = [{'key': 'arden_level', 'label': 'Which level will you compare? Programme-wide awards are calculated only on the complete programme basis.', 'type': 'select', 'options': [{'value': k, 'label': 'Level ' + k} for k in ('4', '5', '6')]}] + questions
        return questions
    questions = previous.eligibility_questions(request, raw)
    if raw and raw.get('importer') == CAMPUS_IMPORTER:
        questions = [dict(q, label='Is your eligible programme at least 120 credits?') if q.get('key') == 'arden_programme_at_least_120_credits' else q for q in questions]
    return questions


def calculate_deposit(*, tuition, academic_year):
    try:
        raw = tuition.source_metadata.raw_fees
    except AttributeError:
        raw = {}
    if raw.get('importer') in ONLINE_IMPORTERS:
        return dict(available=False, amount=None, currency=tuition.currency, academic_year=academic_year,
                    source_url=raw.get('course_source_url'), policy_checked_at=None,
                    rule='campus_policy_not_applicable', is_estimate=False,
                    label='Campus pre-CAS deposit policy does not apply to online study',
                    note='Confirm online payment requirements with Arden; no numeric deposit has been verified.', missing_facts=[])
    return previous.calculate_deposit(tuition=tuition, academic_year=academic_year)

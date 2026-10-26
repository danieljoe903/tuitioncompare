"""Select a reviewed published fee; never infer fee status from nationality."""
from decimal import Decimal, InvalidOperation

FEE_CATEGORIES = {
    'international': 'International',
    'ruk_roi': 'England/Wales/N Ireland/Rep of Ireland',
    'scotland': 'Scotland',
}


def select_published_fee(source, fee_status, academic_year):
    def unavailable(reason):
        return {'available': False, 'amount': None, 'currency': None,
                'source_fee_category': None, 'reason': reason}

    if fee_status not in FEE_CATEGORIES:
        return unavailable('Fee assessment required.')
    if source is None or source.academic_year != academic_year:
        return unavailable('No reviewed published fee for this academic year.')
    raw = source.raw_fees
    if not isinstance(raw, dict) or not isinstance(raw.get('fee_categories'), list):
        return unavailable('Published fee categories are unavailable.')
    category = FEE_CATEGORIES[fee_status]
    matches = [r for r in raw['fee_categories'] if isinstance(r, dict)
               and r.get('source_fee_category') == category
               and r.get('academic_year') == academic_year]
    if len(matches) != 1:
        return unavailable('The selected category is missing or ambiguous.')
    row = matches[0]
    if (row.get('amount_requires_review') is not False
            or row.get('footnote_marker') or row.get('currency') != 'GBP'):
        return unavailable('The published fee requires review.')
    try:
        amount = Decimal(str(row['annual_tuition']))
        if (not amount.is_finite() or amount <= 0
                or amount != amount.quantize(Decimal('.01'))):
            return unavailable('Invalid published fee.')
    except (KeyError, InvalidOperation, ValueError, TypeError):
        return unavailable('Invalid published fee.')
    return {'available': True, 'amount': amount, 'currency': row['currency'],
            'source_fee_category': category, 'academic_year': academic_year,
            'source_url': source.source_url, 'annotation': row.get('annotation', ''),
            'reason': ''}

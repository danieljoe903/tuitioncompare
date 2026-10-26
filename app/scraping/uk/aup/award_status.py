"""Status of published award cycles, distinct from individual eligibility."""
from datetime import date

SOURCE = 'https://www.aup.ac.uk/international/finance'
DEADLINE = date(2026, 2, 25)

def competitive_award_notices(as_of=None):
    day = as_of or date.today()
    ended = day > DEADLINE
    return [dict(
        name=name, status='ended' if ended else 'application_cycle_open',
        label='Has ended' if ended else 'Published application cycle open',
        application_deadline=DEADLINE.isoformat(), source_url=SOURCE,
        applicable_academic_year=None, automatic_deduction=False,
        message=('The published application deadline was 25 February 2026. '
                 'This application cycle has ended. September 2027 availability '
                 'has not been confirmed. Existing award letters require separate assessment.'
                 if ended else 'Selection and an actual university award are required.'),
    ) for name in [
        'Creative Scholarships',
        'Pro-Vice-Chancellor’s Arts University Plymouth Creative Excellence Award',
        'Vice-Chancellor’s Arts University Plymouth Excellence Award',
    ]]

"""Offline date/status tests; no database writes."""
from datetime import date
from app.scraping.uk.aup.award_status import competitive_award_notices

for day, expected in [(date(2026,2,24),'application_cycle_open'),
                      (date(2026,2,25),'application_cycle_open'),
                      (date(2026,2,26),'ended'), (date(2027,9,1),'ended')]:
    rows = competitive_award_notices(day)
    assert len(rows) == 3
    assert all(r['status'] == expected and r['automatic_deduction'] is False for r in rows)
    assert all(r['applicable_academic_year'] is None for r in rows)
print('PASS: 12 award-cycle date scenarios. No database records changed.')

"""Read-only coverage classification; no student eligibility is assumed."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import re

OWNER = 'arden_final_handoff_v1'
EXPECTED = dict(total_course_records=73, active_courses=72, inactive_courses=1,
                verified_international_fees=53, pending_international_fees=19,
                verified_fee_courses_with_conditional_award_scope=39,
                verified_fee_courses_without_conditional_award_scope=14)


def amount(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def mode_of(raw):
    # The first campus PG importer predates study_mode/study_options fields.
    if (not raw.get('study_mode') and raw.get('importer') == 'arden_campus_pg_v1'
            and raw.get('course_kind') == 'postgraduate_taught'):
        return 'campus'
    return raw.get('study_mode')


def options_of(raw):
    if raw.get('study_options'):
        return raw['study_options']
    if (raw.get('importer') == 'arden_campus_pg_v1' and isinstance(raw.get('campus_intakes'), dict)):
        return [dict(location=campus, intake=intake) for campus, intakes in raw['campus_intakes'].items()
                if isinstance(intakes, list) for intake in intakes]
    return []


def fee_check(tuition, source):
    if not source:
        return False, 'No saved international tuition source.', None
    raw = source.get('raw_fees') or {}
    if not isinstance(raw, dict):
        return False, 'Invalid fee metadata.', None
    if raw.get('verified') is not True:
        return False, raw.get('verification_reason') or raw.get('fee_note') or 'Applicable route/intake/fee remains unverified.', None
    if (tuition.get('currency') != 'GBP' or raw.get('currency') != 'GBP'
            or not source.get('source_url') or not re.fullmatch(r'\d{4}/\d{2}', source.get('academic_year') or '')
            or raw.get('academic_year') != source.get('academic_year')
            or raw.get('source_fee_category') != 'International'):
        return False, 'Currency, source, academic year or international fee category inconsistent.', None
    basis = source.get('fee_basis')
    mode = mode_of(raw)
    stored = amount(tuition.get('tuition_fee'))
    if basis == 'annual_tuition' and mode == 'campus':
        published = amount(raw.get('annual_tuition'))
        programme = published if raw.get('course_kind') == 'undergraduate_topup' else None
    elif basis == 'total_programme_fee' and mode == 'online':
        published = amount(raw.get('total_programme_fee'))
        programme = published
    elif basis == 'per_level' and mode == 'online':
        levels = raw.get('fees_by_level', {})
        if not isinstance(levels, dict) or set(levels) != {'4', '5', '6'} or not all(amount(v) for v in levels.values()):
            return False, 'Incomplete or invalid Levels 4–6 fee schedule.', None
        published = amount(levels.get(str(raw.get('stored_fee_level'))))
        programme = sum(amount(v) for v in levels.values())
    else:
        return False, 'Unsupported or inconsistent mode/fee basis.', None
    if not stored or not published or stored != published:
        return False, 'Stored tuition differs from the published amount for its fee basis.', None
    options = options_of(raw)
    if not options or not all(isinstance(o, dict) and re.fullmatch(r'\d{4}-\d{2}', o.get('intake', '')) for o in options):
        return False, 'Advertised intake options not verified.', None
    if mode == 'online':
        if not raw.get('fee_country_scope') or not raw.get('online_intakes'):
            return False, 'Online country or intake scope missing.', None
        if not any(o.get('location') == 'Online' and o['intake'] in raw['online_intakes'] for o in options):
            return False, 'Online intake scope does not match application evidence.', None
    elif not raw.get('campus_intakes'):
        return False, 'Campus intake scope missing.', None
    return True, '', programme


def scoped_rule(rule, course_id, source, programme):
    r = rule.get('rules') or {}
    if not isinstance(r, dict):
        return False
    value = amount(r.get('value'))
    cond = r.get('conditions') or {}
    if (rule.get('verified') is not True or not rule.get('source_url') or not value
            or r.get('academic_year') != source.get('academic_year') or r.get('currency') != 'GBP'
            or r.get('benefit') != 'tuition' or r.get('fee_basis_confirmed') is not True
            or r.get('kind') not in {'fixed', 'fixed_maximum', 'percentage'}
            or (r.get('kind') == 'percentage' and (value > 100 or r.get('percentage_basis') != 'original_tuition'))
            or r.get('award_status') not in {'conditional', 'automatic', 'competitive'}
            or r.get('stacking') not in {'exclusive', 'explicit'} or not isinstance(r.get('compatible_ids'), list)
            or not isinstance(r.get('study_years'), list) or 1 not in r['study_years']
            or not isinstance(cond, dict) or not cond or not all(isinstance(v, list) and v for v in cond.values())
            or course_id not in cond.get('course_id', []) or 'INTL' not in cond.get('fee_status', [])):
        return False
    raw = source['raw_fees']
    options = options_of(raw)
    if not any((not cond.get('arden_intake') or o.get('intake') in cond['arden_intake'])
               and (not cond.get('arden_campus') or o.get('location') in cond['arden_campus']) for o in options):
        return False
    period = r.get('comparison_period')
    if mode_of(raw) == 'online':
        if period != 'total_programme':
            return False
        if 'arden_online_gross_quote' in cond and not any(amount(v) == programme for v in cond['arden_online_gross_quote']):
            return False
    elif period not in {None, 'annual'}:
        return False
    return True


def analyse(data, reference=None):
    references = {r['course_id']:r for r in (reference or {}).get('courses', [])}
    tuition_by = defaultdict(list)
    source_by = defaultdict(list)
    for t in data['tuitions']:
        if t['residency'] == 'INTL':
            tuition_by[t['course_id']].append(t)
    for s in data['sources']:
        source_by[s['tuition_id']].append(s)
    rows=[]; inactive=[]; counts=Counter(); bases=Counter(); years=Counter(); anomalies=[]
    for policy in data['deposit_policies']:
        pr=policy.get('rules') or {}
        if policy.get('verified') is True and (not isinstance(pr,dict) or pr.get('kind')!='application_advised'):
            anomalies.append('Deposit policy '+str(policy.get('id'))+': verified policy kind is outside this audit’s individually-advised Arden policy; review manually.')
    for course in data['courses']:
        cid=course['course_id']
        if not course['is_active']:
            inactive.append(course)
            continue
        ts=tuition_by[cid]; source=None; raw={}; programme=None
        if len(ts) != 1:
            valid=False; reason=f'Expected one international tuition row; found {len(ts)}.'
        elif len(source_by[ts[0]['id']]) != 1:
            valid=False; reason=f'Expected one international source; found {len(source_by[ts[0]["id"]])}.'
        else:
            source=source_by[ts[0]['id']][0]; raw=source.get('raw_fees') or {}
            if not isinstance(raw,dict):raw={}
            valid,reason,programme=fee_check(ts[0],source)
        rules=[r for r in data['scholarship_rules'] if valid and scoped_rule(r,cid,source,programme)]
        policies=[]
        if valid and mode_of(raw) == 'campus':
            for p in data['deposit_policies']:
                pr=p.get('rules') or {}
                if (p.get('verified') is True and p.get('source_url') and p.get('currency')=='GBP'
                    and p.get('academic_year')==source['academic_year'] and isinstance(pr,dict)
                    and pr.get('kind')=='application_advised' and cid in pr.get('course_ids',[])):
                    policies.append(p)
        if not valid: deposit='pending_route_or_policy_confirmation'
        elif policies: deposit='individual_amount_advised_during_application'
        elif raw.get('study_mode')=='online': deposit='campus_policy_not_applicable_online_payment_requirements_unverified'
        else: deposit='pending_policy_scope'
        ref=references.get(cid)
        if valid and ref: anomalies.append(cid+': now verified but still listed in the old pending reference; review the change.')
        if not valid and not ref: anomalies.append(cid+': pending fee outside the previous 19-case reference.')
        if raw.get('verified') is True and not valid: anomalies.append(cid+': verified flag failed consistency checks: '+reason)
        pending_reason=(ref or {}).get('finding') or reason
        row=dict(course_id=cid,title=course['title'],location=course.get('location'),
            intake_months=course.get('intake_months'),start_month=course.get('start_month'),start_year=course.get('start_year'),
            fee_status='verified' if valid else 'pending',fee_basis=source.get('fee_basis') if source else None,
            academic_year=source.get('academic_year') if source else None,
            programme_total_derived_from_levels=str(programme) if valid and source['fee_basis']=='per_level' else None,
            fee_review_reason='' if valid else pending_reason,fee_consistency_error=reason,
            review_category=(ref or {}).get('category') if not valid else None,next_action=(ref or {}).get('next_action') if not valid else None,
            conditional_scholarship_rules=rules,
            scholarship_status='verified_conditional_scope' if rules else 'pending_applicability' if valid else 'pending_verified_route_and_tuition',
            scholarship_review=raw.get('scholarship_review',{}),online_alumni_review=raw.get('online_alumni_review',{}),
            deposit_status=deposit,deposit_policy_ids=[p['id'] for p in policies],numeric_deposit_verified=False,
            stored_numeric_discount=ts[0].get('scholarship') if len(ts)==1 else None,
            stored_numeric_deposit=ts[0].get('tuition_deposit') if len(ts)==1 else None)
        rows.append(row);counts[row['fee_status']]+=1
        if valid:bases[row['fee_basis']]+=1;years[row['academic_year']]+=1
    verified=[r for r in rows if r['fee_status']=='verified']
    gaps=[r for r in verified if not r['conditional_scholarship_rules']]
    summary=dict(total_course_records=len(data['courses']),active_courses=len(rows),inactive_courses=len(inactive),
        verified_international_fees=counts['verified'],pending_international_fees=counts['pending'],
        verified_fee_bases=dict(bases),verified_fee_years=dict(years),
        scholarship_records=len(data['scholarships']),scholarship_rule_records=len(data['scholarship_rules']),
        verified_scholarship_rule_records=sum(r.get('verified') is True for r in data['scholarship_rules']),
        unverified_scholarship_rule_records=sum(r.get('verified') is not True for r in data['scholarship_rules']),
        verified_fee_courses_with_conditional_award_scope=len(verified)-len(gaps),
        verified_fee_courses_without_conditional_award_scope=len(gaps),
        deposit_policy_records=len(data['deposit_policies']),
        verified_deposit_policy_records=sum(p.get('verified') is True for p in data['deposit_policies']),
        verified_fee_courses_with_individually_advised_deposit_scope=sum(bool(r['deposit_policy_ids']) for r in verified),
        verified_fee_online_courses_campus_deposit_policy_not_applicable=sum(r['deposit_status'].startswith('campus_policy_not_applicable') for r in verified),
        verified_fee_courses_with_pending_campus_deposit_scope=sum(r['deposit_status']=='pending_policy_scope' for r in verified),
        verified_numeric_deposit_amounts=0)
    differences={k:dict(expected=v,actual=summary[k]) for k,v in EXPECTED.items() if summary[k]!=v}
    return dict(owner=OWNER,status='reviewed_with_outstanding_cases',summary=summary,course_results=rows,
                inactive_courses=inactive,pending_fee_cases=[r for r in rows if r['fee_status']=='pending'],
                verified_fee_scholarship_scope_gaps=gaps,expected_count_differences=differences,anomalies=anomalies,
                notes=['Scope coverage is conditional and does not confirm a real student award.',
                       'Per-level schedules and derived programme totals are not annual tuition.',
                       'A verified individually-advised deposit policy is not a verified numeric deposit.',
                       'Online campus-policy exclusion does not mean no payment is required.',
                       'Missing scholarship scope does not establish that no funding exists.',
                       'Existing numeric discount/deposit fields are reported, not accepted as verified evidence.',
                       'This audit checks saved metadata consistency; it does not refresh websites or rerun API tests.'])

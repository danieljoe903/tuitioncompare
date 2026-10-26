"""Pure coverage aggregation and preservation of the combined progress register."""
import json
import re
from collections import Counter

OWNER='aub_final_handoff_v1'
START='<!-- university-audit:UK005:final:start -->'
END='<!-- university-audit:UK005:final:end -->'
PRIOR_NAMES=['Abertay University (UK001)','Aberystwyth University (UK002)','Anglia Ruskin University (UK003)','Arden University (UK004)']

def summarize(rows,total,active,rules,policies):
    verified=[r for r in rows if r['fee_verified']]
    scoped=[r for r in verified if r['conditional_award_rule_ids']]
    return dict(total_course_records=total,active_courses=active,inactive_courses=total-active,
        verified_international_fees=len(verified),pending_international_fees=sum(not r['fee_verified'] for r in rows),
        verified_fee_bases=dict(Counter(r['fee_basis'] for r in verified)),
        verified_fee_years=dict(Counter(r['academic_year'] for r in verified)),
        scholarship_rule_records=len(rules),verified_scholarship_rule_records=sum(r['verified'] for r in rules),
        unverified_scholarship_rule_records=sum(not r['verified'] for r in rules),
        verified_fee_courses_with_conditional_award_scope=len(scoped),
        verified_fee_courses_without_conditional_award_scope=len(verified)-len(scoped),
        verified_fee_courses_with_actual_amount_award_scope=sum('confirmed_amount' in r['award_kinds'] for r in verified),
        verified_fee_courses_with_early_payment_waiver_scope=sum(r['early_payment_scope'] for r in verified),
        verified_fee_courses_with_fixed_online_progression_scope=sum(r['online_progression_scope'] for r in verified),
        foundation_year_zero_competitive_scholarship_pending=sum(r['foundation_year_zero'] for r in verified),
        deposit_policy_records=len(policies),verified_deposit_policy_records=sum(p['verified'] for p in policies),
        verified_fee_courses_with_numeric_payment_available=sum(r['payment']['available'] for r in verified),
        verified_fee_courses_without_verified_numeric_payment=sum(not r['payment']['available'] for r in verified),
        verified_fee_courses_with_confirmed_numeric_campus_cas_deposit=sum(r['payment']['available'] and r['payment'].get('is_cas_deposit') is True for r in verified))

def cell(value):return str(value).replace('|','/').replace('\n',' ')

def section(report):
    s=report['summary']
    lines=[START,'## UK — Arts University Bournemouth (UK005): final review pass / saved-data audit','',
        'Generated: '+report['generated_at'],'',
        'Status: reviewed with outstanding cases. This supersedes the earlier first-30 AUB working totals; it does not mark all fees or applicant awards confirmed. Earlier university sections retain their own provenance.','',
        '| Measure | Audited value |','|---|---:|']
    for k,v in s.items():lines.append('| '+cell(k.replace('_',' '))+' | '+cell(json.dumps(v) if isinstance(v,dict) else v)+' |')
    lines+=['','### Six route/intake cases and any new fee gaps','','| Course | Current reason | Next action |','|---|---|---|']
    for r in report['pending_fee_cases']:lines.append('| '+cell(r['course_id']+' — '+r['title'])+' | '+cell(r['fee_reason'])+' | '+cell(r['next_action'])+' |')
    lines+=['','### Scholarships, payment requirements and comparison behaviour','',
        '- Competitive campus awards: actual AUB-granted amount required, up to GBP 5,000 UG / GBP 3,000 PG. Never deduct the maximum automatically. AUB official award correspondence and saved personal facts determine an individual deduction.',
        '- Four reviewed online MAs: GBP 9,975 programme total; conditional GBP 1,995 progression bursary gives GBP 7,980. Never display the total as an annual fee.',
        '- Fixed early-payment waiver: GBP 1,500 against reviewed annual campus tuition, net GBP 18,450 from GBP 19,950, only after confirmed full annual payment by 1 August 2027 and no other AUB award. No application or award-letter amount is required for this payment waiver. Future/unconfirmed payments do not qualify.',
        '- The eight integrated Foundation Year 0 fees are verified. Competitive Year 0 scholarship applicability remains unconfirmed; the separately verified early-payment waiver is conditional. Foundation Diploma awards must not be substituted.',
        '- MArch early-payment waiver scope remains unconfirmed because the published PG deadline names MA courses. This does not remove its competitive actual-amount award scope.',
        '- Campus deposit requirements remain unconfirmed for 2027/28. The collected policy is labelled 2026/27 with its next review in February 2027. Keep the confirmation message, not a guessed numeric deposit or zero.',
        '- Four online routes publish GBP 500 acceptance payment, included in GBP 9,975 tuition. It is not a campus pre-CAS deposit or an extra GBP 500 charge.',
        '- No real student award or payment was confirmed by synthetic tests. No count of applicant-confirmed awards is inferred from rule coverage.',
        '- Comparison form: no scholarship eligibility questions; blank residence retains matching results. Show verified full tuition, applicable saved deduction/net, payment amount or confirmation message and a link/message directing users to the scholarship page. Questions and actual competitive grant amounts belong on the scholarship page.',
        '- Scholarship-page UI/persistence remains a separate frontend implementation task. Saved-answer adapter contracts exist; this audit does not build that page.',
        '- Durations/intakes, replacement history and validation caveats remain in per-course sources. An annual price is not a programme total. C_UK005_39 remains subject to validation; unverified PG durations are not silently certified.','',
        'Audit file: `'+report['audit_path']+'`','',
        'Source references: `data/universities/uk/aub/final_review_reference.json` and the saved per-course/rule evidence.','',
        'Supplied synthetic test results: first30 810; remaining13 57; online/foundation 88; foundation/Dance 26; early-payment adapter 585. They are separate runs with overlapping guards, not counts of students or independent awards.','',
        'Next UK university (live inventory): '+cell(report.get('next_university_id') or 'none found')+'. Ireland follows the UK workflow. The combined UK/Ireland PDF remains deferred until scraping is finished.']
    if report['anomalies']:lines+=['','### Audit anomalies requiring review','']+['- '+cell(a) for a in report['anomalies']]
    lines += [END,'']
    return '\n'.join(lines)

def merge_register(text,report):
    if not all(name in text for name in PRIOR_NAMES):raise ValueError('Combined register must contain all four previous universities; no partial overwrite.')
    if START in text or END in text:
        if text.count(START)!=1 or text.count(END)!=1 or text.index(END)<text.index(START):raise ValueError('AUB final markers are inconsistent.')
        a=text.index(START);b=text.index(END)+len(END)
        text=text[:a]+section(report).rstrip()+text[b:]
    else:text=text.rstrip()+'\n\n'+section(report)
    # Only update AUB's named inventory row. Prior university narratives remain intact.
    text=re.sub(r'(?m)^\| UK005 \| Arts University Bournemouth \|[^\n]*$',
        '| UK005 | Arts University Bournemouth | Reviewed with outstanding cases; see final AUB audit |',text)
    text=text.replace('| UK | 146, confirmed by user database export | 4 | 142 | Abertay, Aberystwyth, ARU and Arden reviewed with outstanding cases retained |',
        '| UK | 146, confirmed by user database export | 5 | 141 | Abertay, Aberystwyth, ARU, Arden and AUB reviewed with outstanding cases retained |')
    text=text.replace('The 142 UK universities are a pending queue','The 141 UK universities are a pending queue')
    return text

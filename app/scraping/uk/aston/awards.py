"""First-year Aston award rules scoped to reviewed routes and exact intakes."""
from datetime import datetime
from zoneinfo import ZoneInfo
OWNER='aston_first30_awards_v1'
WEB='https://www.aston.ac.uk/international/scholarships'
FUNDING='https://www.aston.ac.uk/undergraduate/your-application/funding'
SPONSOR='https://www.aston.ac.uk/international/students-applying/international-sponsored-students'
UG_PDF='https://www.aston.ac.uk/sites/default/files/V3%20UG%20ICS%20Terms%20and%20Conditions%20September%202027.pdf'
JAN_PDF='https://www.aston.ac.uk/sites/default/files/PG%20ICS%20Terms%20and%20Conditions%20January%202027.pdf'
SEP_PDF='https://www.aston.ac.uk/sites/default/files/V2%20PG%20ICS%20Terms%20and%20Conditions%20September%202027.pdf'
CAD_PDF='https://www.aston.ac.uk/sites/default/files/SACCS%20Terms%20and%20Conditions%202027%20V2_3.pdf'
EXCLUDED={'C_UK007_17','C_UK007_24','C_UK007_31','C_UK007_32','C_UK007_33','C_UK007_50'}
COMMON={
 'aston_overseas_confirmed':'Has Aston confirmed Overseas fee status for this programme?',
 'aston_offer_conditions_met':'Have all conditions of your exact full-time Birmingham offer been met by the required deadlines?',
 'aston_self_funded':'Are your tuition fees fully self-funded, with no sponsorship, employer funding or reimbursement?',
 'aston_deposit_paid_on_time':'Has Aston confirmed receipt of the required programme deposit by your offer deadline?',
 'aston_enrolment_confirmed':'Has Aston confirmed successful enrolment by the latest arrival date, with continuing attendance?',
 'aston_permission_confirmed':'Has Aston confirmed the required CAS and valid permission to study, where applicable?',
 'aston_full_first_taught_year':'Is this the original first taught year, with no placement, repeat, suspension, withdrawal or pro-rata adjustment?',
 'aston_no_combined_awards':'Will only the single award determined by Aston be held, with no other internal or external scholarship, bursary or fee discount combined?',
}
EXTRA={
 'aston_ug_direct_nonpartner':'Are you entering Year 1 directly, outside every formal partnership, progression or articulation agreement?',
 'aston_pg_no_dedicated_partner_award':'Has Aston confirmed that no separate dedicated partnership/progression fee arrangement excludes you from PG ICS?',
 'aston_ics_applied':'Has Aston confirmed the Community Scholarship has been formally applied for this exact programme/intake?',
 'aston_cadbury_application_accepted':'Has Aston confirmed your official Cadbury application was submitted and eligible within the applicable deadline?',
 'aston_cadbury_awarded_applied':'Has Aston awarded and applied the GBP 10,000 Cadbury Scholarship to this exact programme/intake and confirmed it is the applicable single award?',
}
EXTRA.update({
 'aston_topup_community_confirmed':'Has Aston confirmed the single GBP 3,000 Community payment applies to your exact final-year Top-up offer and September 2027 intake?',
 'aston_topup_nonpartner':'Has Aston confirmed that no excluded formal partnership, progression or articulation agreement applies to your Top-up entry?',
 'aston_mba_deposit_required_confirmed':'Does your exact MBA offer confirm the published GBP 5,000 acceptance deposit is required?',
 'aston_no_deposit_waiver_confirmed':'Has Aston confirmed no loan, government sponsorship or other deposit waiver changes your payment requirement?',
})
FACTS={**COMMON,**EXTRA}

def specs(manifest):
    ug=sorted(k for k,v in manifest.items() if v['level']=='UG' and k not in EXCLUDED and k!='C_UK007_26')
    jan=sorted(k for k,v in manifest.items() if v['level']=='PG' and v['start_month']=='January' and k not in EXCLUDED and k!='C_UK007_26')
    sep=sorted(k for k,v in manifest.items() if v['level']=='PG' and v['start_month']=='September' and k not in EXCLUDED and k!='C_UK007_26')
    groups=[('ug_sep',ug,'UG','September','2027/28','3000.00',UG_PDF),
            ('pg_jan',jan,'PG','January','2026/27','4000.00',JAN_PDF),
            ('pg_sep',sep,'PG','September','2027/28','5500.00',SEP_PDF)]
    out={}
    for key,ids,level,month,year,amount,url in groups:
        for kind in ['ics','cadbury']:
            k=kind+'_'+key
            facts=[*COMMON]
            facts+=['aston_ics_applied', 'aston_ug_direct_nonpartner' if level=='UG' else 'aston_pg_no_dedicated_partner_award'] if kind=='ics' else ['aston_cadbury_application_accepted','aston_cadbury_awarded_applied']
            out[k]=dict(key=k,name='Aston '+('International Community Scholarship' if kind=='ics' else 'Sir Adrian Cadbury Chancellor’s Scholarship')+' — '+level+' '+month+' 2027 — reviewed30',
                course_ids=ids,level=level,month=month,academic_year=year,amount=amount if kind=='ics' else '10000.00',
                source_url=url if kind=='ics' else CAD_PDF,facts=facts,kind=kind,
                deadline='2026-11-01' if kind=='cadbury' and month=='January' else None)
    if 'C_AST007_01' in manifest:
        out['ics_topup_sep']=dict(key='ics_topup_sep', name='Aston International Community Scholarship — Top-up September 2027 — reviewed',
            course_ids=['C_AST007_01'],level='UG',month='September',academic_year='2027/28',amount='3000.00',
            source_url=WEB,facts=[*COMMON,'aston_ics_applied','aston_topup_community_confirmed','aston_topup_nonpartner'],
            kind='ics',deadline=None,fee_basis='annual_tuition')
    if 'C_UK007_26' in manifest:
        out['ics_mph_sep']=dict(key='ics_mph_sep',name='Aston International Community Scholarship — MPH September 2027 — reviewed',
            course_ids=['C_UK007_26'],level='PG',month='September',academic_year='2027/28',amount='5500.00',
            source_url=SEP_PDF,facts=[*COMMON,'aston_ics_applied','aston_pg_no_dedicated_partner_award'],
            kind='ics',deadline=None,fee_basis='full_time_course_fee')
    return out

def make_rule(spec):
    conditions={k:[True] for k in spec['facts']}
    conditions.update(course_id=spec['course_ids'],fee_status=['INTL'])
    return dict(importer=OWNER,spec_key=spec['key'],benefit='tuition',kind='fixed',value=spec['amount'],currency='GBP',
        academic_year=spec['academic_year'],study_years=[1],conditions=conditions,award_status='conditional',
        stacking='exclusive',compatible_ids=[],percentage_basis='original_tuition',fee_basis_confirmed=True,
        fee_basis=spec.get('fee_basis','first_year_tuition' if spec['level']=='UG' else 'annual_tuition'),
        intake_month=spec['month'],intake_year=2027,application_deadline=spec['deadline'],
        deadline_blocks_new_applications_only=True,questions_location='scholarship_page',
        eligibility_questions=[dict(key=k,type='boolean',label=FACTS[k]) for k in spec['facts']],
        scope_note='Only the single reviewed first-year fee and exact intake. Later taught-year progression, special stacking, sponsor exceptions and pro-rata changes require separate review.')

def deposit_rule(year,manifest):
    return dict(importer=OWNER,kind='offer_letter',academic_year=year,currency='GBP',fee_status='INTL',
        course_ids=sorted(k for k,v in manifest.items() if v['academic_year']==year),included_in_tuition=True,
        amount=None,mba_acceptance_deposits={cid:dict(amount='5000.00',currency='GBP',payment_stage='offer_acceptance',included_in_tuition=True,source_url=manifest[cid]['source_url']) for cid in ('C_UK007_24','C_UK007_50') if cid in manifest and manifest[cid]['academic_year']==year},note='Deposit amounts remain offer-letter specific. The amount and deadline are stated in the Aston offer. Student-loan and government-sponsor exceptions require Aston assessment; no numeric deposit is inferred. A scholarship is not a deposit payment.')

def notices():
    day=datetime.now(ZoneInfo('Europe/London')).date().isoformat()
    return [dict(name='Cadbury — January 2027',status='ended' if day>'2026-11-01' else 'open_until_published_deadline',
                 label='Has ended' if day>'2026-11-01' else 'Applications: deadline 1 November 2026, 23:59 UK time',
                 note='Ending the application window does not cancel an already confirmed award.'),
            dict(name='Cadbury — September 2027',status='dates_unconfirmed',label='Application dates to be confirmed',
                 note='Published award terms exist; do not assume the application window is open.'),
            dict(name='Community Scholarships — reviewed 2027 intakes',status='conditional_offer_deadline',
                 label='Automatic under governing terms; conditions apply',
                 note='The UG webpage describes a competitive application, but V3 governing terms state automatic award and take precedence. Deposit and enrolment confirmation are required.')]

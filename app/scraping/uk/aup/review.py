"""Build explicit route/fee proposals, never infer awards from legacy amounts."""
import re

INTL='https://www.aup.ac.uk/international/finance'
UG='https://www.aup.ac.uk/study/undergraduate/fees-financing-and-funding'
PG='https://www.aup.ac.uk/study/postgraduate/fees-finance-funding'
APPLY='https://www.aup.ac.uk/apply'
PENDING={'C_AUP006_01':'A standard Graphic Design BA and general advanced-entry guidance do not confirm the stored separate top-up route.',
         'C_UK006_18':'No exact current MA Drawing route/intake established; do not replace with BA Painting, Drawing & Printmaking or MA Fine Art automatically.',
         'C_UK006_24':'No exact current MA Painting route/intake established; a related Fine Art practice is not an accepted replacement.'}


def compact(value):return ' '.join(value.split())


def plan(data,manifest,application_text):
    if data.get('university_id')!='UK006':raise ValueError('Expected UK006 source report.')
    pages={p['source_url']:p for p in data['pages'] if p.get('http_status')==200}
    intl=compact(pages[INTL]['text']);ug=compact(pages[UG]['text']);pg=compact(pages[PG]['text']);app=compact(application_text)
    if not all(x in app for x in ['September 2027','Undergraduate BA','Postgraduate Masters']):raise ValueError('Application page does not establish the reviewed September 2027 cycle.')
    try:
        isection=intl.split('International Fees for 2027/28',1)[1].split('How to pay your fees',1)[0]
        usection=ug.split('Undergraduate Fees 2027/28',1)[1].split('Student Loans',1)[0]
        psection=pg.split('Postgraduate Fees 2027/28',1)[1].split('Bursaries',1)[0]
    except IndexError as exc:raise ValueError('Fee-cohort section missing.') from exc
    if not re.search(r'BA \(Hons\) and Integrated Foundation Year £18,000 per year',isection):raise ValueError('International BA fee changed.')
    if not re.search(r'Masters Degrees £18,000 per year \(full time, 1 year course\)',isection):raise ValueError('International MA fee changed.')
    if not re.search(r'CertHE \(EU & International students\) £17,500 per year \(1 year course\)',usection):raise ValueError('International CertHE price changed.')
    if not re.search(r'BA \(Hons\) Degrees \(EU & International students\) Full Time: £18,000 per year',usection):raise ValueError('UG/international BA source discrepancy.')
    if not re.search(r'Masters degree \(EU & International students\*?\) Full-time\s*[-–]\s*£18,000',psection):raise ValueError('PG/international MA source discrepancy.')
    snapshot=data['database_snapshot'];courses={c['course_id']:c for c in snapshot['courses']}
    tuitions={}
    for t in snapshot['tuitions']:
        if t['residency']=='INTL':
            if t['course_id'] in tuitions:raise ValueError('Duplicate international row.')
            tuitions[t['course_id']]=t
    proposals=[];blocked=[]
    for m in manifest:
        cid=m['course_id']
        try:
            c=courses[cid];t=tuitions[cid];p=pages[m['source_url']];text=compact(p['text'])
            if not c['is_active'] or c['university_id']!='UK006' or c['level']!=m['level'] or t['currency']!='GBP':raise ValueError('Stored ownership, level, currency or active status differs.')
            if p['heading']!=m['title'] or p['sha256']!=m['source_sha256']:raise ValueError('Reviewed course capture changed.')
            cert=m['title'].startswith('CertHE ');pgcourse=m['level']=='PG'
            expected=(r'Course Duration 1 year; full-time' if cert or pgcourse else r'Course Duration Full-time:\s*3 years')
            if not re.search(expected,text,re.I):raise ValueError('Expected standard full-time duration not isolated.')
            code=re.search(r'UCAS Code Course:\s*([A-Z0-9]{4})\b',text)
            if not pgcourse and code is None:raise ValueError('Exact UCAS course code missing.')
            if cert:
                links=p.get('application_links',[])
                if not any('ucas.com/explore/courses/' in l['url'] and 'studyYear=2027' in l['url'] for l in links):raise ValueError('CertHE 2027 application link not captured.')
            amount='17500.00' if cert else '18000.00'
            after=dict(title=m['title'],course_url=m['source_url'],duration='1 Year' if cert or pgcourse else '3 Years',start_year=2027,start_month='September',intake_months='September')
            if code:after['ucas_cao_code']=code.group(1)
            changes={k:{'before':c.get(k),'after':v} for k,v in after.items() if c.get(k)!=v}
            proposals.append(dict(course_id=cid,classification='reviewed_fee_proposal',level=m['level'],qualification='CertHE' if cert else 'MA' if pgcourse else 'BA (Hons)',
                before_course=c,before_international_tuition=t,proposed_course=after,course_changes=changes,
                tuition_fee_before=str(t['tuition_fee']),tuition_fee=amount,currency='GBP',academic_year='2027/28',fee_basis='annual_tuition',
                course_source_url=m['source_url'],course_source_sha256=p['sha256'],fee_source_url=UG if not pgcourse else PG,
                fee_source_sha256=pages[UG if not pgcourse else PG]['sha256'],international_fee_source_sha256=pages[INTL]['sha256'],
                intake_source_url=m['source_url'] if cert else APPLY,intake_evidence='Official exact-course 2027 UCAS application link' if cert else 'Exact advertised course plus AUP September 2027 BA/MA application cycle',
                subject_to_validation=bool(re.search(r'subject to validation',text,re.I)),
                scholarship_review={'status':'cohort_applicability_pending','reason':'Competitive awards page gives 25 February 2026 deadline, not a verified 2027 award cycle. No automatic maximum deduction. PG Alumni 15% eligibility/combination terms need separate confirmation.','questions_location':'scholarship_page'},
                deposit_review={'status':'published_policy_candidate','amount':'2000.00','currency':'GBP','payment_stage':'before_cas','requires_student_visa':True,'source_url':INTL,'note':'General current policy on page containing 2027/28 fees. Save a conditional scoped policy only in a reviewed import; remaining tuition payable in full at enrolment. Do not add deposit to tuition.'}))
        except (KeyError,ValueError) as exc:blocked.append(dict(course_id=cid,reason=str(exc)))
    return dict(university_id='UK006',database_changes=False,mappings_applied=False,proposals=proposals,blocked=blocked,
        pending=[dict(course_id=cid,reason=reason) for cid,reason in PENDING.items()],
        legacy_scholarship_review=data.get('existing_award_source_issues',[]),
        summary={'verified_fee_proposals':len(proposals),'blocked':len(blocked),'pending_route_cases':len(PENDING),'automatic_scholarship_deductions':0,'deposit_policy_candidates':1})

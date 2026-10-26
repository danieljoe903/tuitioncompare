"""Read-only AUB route review. No ORM writes, award grants or implied replacements."""
from collections import Counter
from decimal import Decimal
import re
from urllib.parse import urlparse,parse_qs

ONLINE_FEES='https://aub.ac.uk/fees/postgraduate-online'
PG_FEES='https://aub.ac.uk/fees/postgraduate'
AWARDS='https://aub.ac.uk/international/international-scholarships/international-undergraduate-and-postgraduate-scholarships'
FOUNDATION='https://aub.ac.uk/integrated-foundation-year'


def normal(text):return ' '.join(str(text).split())


def exact_fee(page,label,value):
    rows=[r for table in page['tables'] for r in table if len(r)>1 and normal(r[0])==label]
    amounts=[Decimal(re.sub(r'[^0-9.]','',r[1])) for r in rows]
    if amounts.count(Decimal(value))!=1:raise ValueError('Cannot isolate the reviewed fee row and amount: '+label)


PUBLIC_REFERENCE_SHA256='6599923c6f58d1e0712034f2f934e29b658d167a325c5ce55c5fa53533766b1a'


def public_reference_facts(item,page):
    url=urlparse(page.get('source_url',''))
    expected_uuid=item['ucas_url'].split('/courses/')[1].split('/')[0]
    if (page.get('course_id')!=item['course_id'] or page.get('expected_url')!=item['ucas_url']
        or url.hostname!='www.ucas.com' or expected_uuid not in url.path.split('/')
        or parse_qs(url.query).get('studyYear')!=['2027']
        or page.get('heading')!=item['ucas_heading'] or page.get('provider')!='Arts University Bournemouth'
        or page.get('academic_year')!='2027/28' or page.get('intake')!='2027-09'
        or page.get('checked_on')!='2026-10-24' or not page.get('review_reference')):
        raise ValueError('Reviewed public reference does not match this exact route/provider/cohort.')
    result={'evidence_kind':'reviewed_public_listing','intake':'2027-09','source_url':page['source_url'],
            'checked_on':page['checked_on'],'review_reference':page['review_reference'],
            'duration':page['duration'],'subject_to_validation':page.get('subject_to_validation'),
            'validation_status_note':page.get('validation_status_note'),
            'provenance_note':'Structured source review; not a local raw HTML capture.'}
    if item['category']=='online':
        if page.get('duration')!='2 Years' or page.get('mode')!='part_time_online' or page.get('fee_role')!='intake_and_mode_only':raise ValueError('Reviewed online mode/duration is inconsistent.')
        result['ucas_fee_basis_conflict']=page['fee_basis_conflict']
    else:
        if (page.get('mode')!='full_time_campus' or page.get('duration')!='4 Years'
            or page.get('ucas_code')!=item['expected_code'] or page.get('entry_point')!='Foundation'
            or page.get('international_fee')!='19950.00' or page.get('currency')!='GBP'
            or page.get('fee_basis')!='annual_tuition' or page.get('provisional') is not True):raise ValueError('Reviewed foundation route/code/provisional fee is inconsistent.')
        result.update(ucas_code=page['ucas_code'],provisional=True,published_duration=page['published_duration'])
    return result


def ucas_facts(item,page):
    if isinstance(page,dict) and page.get('evidence_kind')=='reviewed_public_listing':return public_reference_facts(item,page)
    if page is None or page.get('error'):raise ValueError('The exact UCAS route capture is unavailable; the collection issue is not proof that the course is unavailable.')
    if page.get('http_status')!=200 or normal(page.get('heading',''))!=item['ucas_heading']:raise ValueError('The UCAS capture does not show the exact expected award heading.')
    text=normal(page['text']);start=text.find(item['ucas_heading'])
    if start<0:raise ValueError('Expected route heading is absent from captured text.')
    body=text[start:];header=body.split('Course summary',1)[0]
    if 'Arts University Bournemouth' not in header or not re.search(r'(?:28 )?September 2027',header):raise ValueError('The selected UCAS route does not establish AUB September 2027 entry.')
    result={'intake':'2027-09','source_url':page['source_url'],'sha256':page['sha256'],'duration':'2 Years' if item['category']=='online' else '4 Years','subject_to_validation':'subject to validation' in body.lower()}
    if item['category']=='online':
        if not re.search(r'2\s+Years',header,re.I) or not re.search(r'Part[- ]Time',header,re.I) or 'Distance learning' not in header:raise ValueError('Cannot isolate the two-year part-time distance route.')
        result['ucas_fee_basis_conflict']='UCAS labels GBP 9,975 per year, while AUB explicitly publishes it as the two-year programme total. Use the direct AUB fee basis; never double the total.'
    else:
        codes=set(re.findall(r'Course code:\s*([A-Z0-9]{4})\b',body))
        if codes!={item['expected_code']}:raise ValueError('The subject-specific foundation UCAS code needs review: '+str(sorted(codes)))
        if not re.search(r'4(?:\s+to\s+5)?\s+Years',header,re.I):raise ValueError('Cannot isolate the foundation route duration.')
        result['ucas_code']=item['expected_code']
        if re.search(r'4\s+to\s+5\s+Years',header,re.I):result['published_duration']='4 to 5 Years; optional placement must be confirmed separately'
        fee=body.split('Fees and funding',1)[-1] if 'Fees and funding' in body else ''
        if not re.search(r'EU\s*&\s*International\s*£\s*19,?950',fee):raise ValueError('Cannot isolate the UCAS international fee for this route.')
        result['provisional']='provisional fee' in fee.lower() or 'subject to change' in fee.lower()
    return result


def build_review(data,manifest,courses,tuitions,captures):
    if len(manifest)!=20 or len({x['course_id'] for x in manifest})!=20:raise ValueError('Expected exactly twenty reviewed course IDs.')
    pages={p['source_url']:p for p in data['pages']};output=[]
    for item in manifest:
        cid=item['course_id'];c=courses[cid]
        if c['title']!=item['stored_title'] or c['level']!=item['level'] or not c['is_active']:raise ValueError(cid+': stored title, level or status changed; review before proceeding.')
        ts=[t for t in tuitions if t['course_id']==cid and t['residency']=='INTL']
        if len(ts)!=1:raise ValueError(cid+': expected one international tuition row.')
        row={'course_id':cid,'title':c['title'],'before':{'course':c,'tuition':ts[0]},'category':item['category'],'proposed':{},'evidence':[],
             'scholarship':{'status':'applicability_pending','deduction':None,'message':'No verified deduction for this exact route has been established by this review.'},
             'deposit':{'available':False,'amount':None,'message':'Do not extend the prior-year campus deposit policy or reuse an unverified stored amount.'}}
        try:
            if item['category']=='pending_identity':
                row.update(status='route_intake_pending',reason=item['reason'])
            elif item['category']=='replacement':
                page=pages[item['course_url']]
                if page['heading']!='MA Fashion Innovation' or 'This course will run from September 2027.' not in page['text'] or 'Study location: AUB Campus' not in page['text']:raise ValueError('Fashion route or recruiting intake changed.')
                exact_fee(pages[PG_FEES],'2027/28 Applicants (Full-time)','19950')
                row.update(status='campus_fee_replacement_proposal',reason='The currently advertised MA Fashion Innovation is proposed explicitly; no automatic title replacement or award-scope extension.')
                row['proposed']={'title':'MA Fashion Innovation','course_url':item['course_url'],'start_month':'September','start_year':2027,'academic_year':'2027/28','currency':'GBP','tuition_fee':'19950.00','fee_basis':'annual_tuition','duration':c['duration'],'duration_verified':False,'mapping':'explicit_replacement_proposal','requires_accept_replacements':True}
                row['evidence']=[{'source_url':item['course_url'],'sha256':page['sha256']},{'source_url':PG_FEES,'sha256':pages[PG_FEES]['sha256']}]
                row['scholarship']={'status':'competitive_actual_amount_scope_proposal','published_maximum':'3000.00','deduction':None,'source_url':AWARDS,'message':'Propose this exact replacement for the reviewed campus PG competitive rule. No automatic maximum deduction; AUB must grant/confirm the actual amount. No scope change has been made.'}
            else:
                facts=ucas_facts(item,captures.get(item['ucas_url']));row['evidence'].append(facts)
                if item['category']=='online':
                    page=pages[item['course_url']]
                    if page['heading']!=c['title'] or 'Study location: Online' not in page['text']:raise ValueError('The direct AUB online course capture does not confirm the exact route.')
                    fee=pages[ONLINE_FEES];exact_fee(fee,c['title'],'9975')
                    if "total fees for our two-year online Master's courses for 2027/28" not in fee['text']:raise ValueError('Cannot confirm the direct AUB two-year fee basis and year.')
                    row.update(status='online_programme_fee_proposal',reason='Exact two-year online route and September 2027 intake; direct AUB programme-total pricing takes precedence over the UCAS generic per-year label.')
                    row['proposed']={'course_url':item['course_url'],'location':'Online','duration':'2 Years','duration_verified':True,'start_month':'September','start_year':2027,'academic_year':'2027/28','currency':'GBP','tuition_fee':'9975.00','fee_basis':'total_programme_fee','annual_fee_available':False,'tax_note':'Country-dependent GST/VAT may apply and is not included in this base price.'}
                    row['evidence'] += [{'source_url':item['course_url'],'sha256':page['sha256']},{'source_url':ONLINE_FEES,'sha256':fee['sha256']}]
                    row['scholarship'].update(message='The online fee page advertises a conditional 20% AUB Alumni progression discount under England support: undergraduate completion within two years. International applicability, exact cohort/route and combination rules require confirmation. Do not infer eligibility or carry over the campus competitive award.',potential_alumni_percentage='20',illustrative_discount_if_confirmed='1995.00',illustrative_programme_net_if_confirmed='7980.00')
                    if '£500 acceptance fee' not in fee['text'] or 'allocated as part of the first payment due for tuition fees' not in fee['text']:raise ValueError('The online acceptance-payment amount or tuition allocation changed.')
                    row['deposit']={'available':True,'amount':'500.00','payment_stage':'acceptance','is_cas_deposit':False,'included_in_tuition':True,'source_url':ONLINE_FEES,'message':'AUB publishes a non-refundable GBP 500 acceptance payment for two-year online courses, allocated to the first tuition payment after study starts. It is not an extra GBP 500 added to the programme total or a campus pre-CAS requirement.'}
                else:
                    row.update(status='foundation_provisional_fee_proposal',reason='Exact subject-specific route/code and September 2027 intake confirmed by UCAS. The listed international fee is provisional; retain that qualifier. The shared AUB foundation page is labelled 2026/27.')
                    row['proposed']={'course_url':item['course_url'],'route_source_url':item['ucas_url'],'ucas_cao_code':facts['ucas_code'],'duration':'4 Years','duration_verified':True,'start_month':'September','start_year':2027,'academic_year':'2027/28','currency':'GBP','published_fee':'19950.00','fee_basis':'annual_tuition','fee_status':'provisional','fee_verified_final':False,'entry_point':'Foundation','subject_to_validation':facts['subject_to_validation'],'validation_status_note':facts.get('validation_status_note')}
                    row['scholarship']['message']='International scholarship treatment of the integrated foundation year/Year 0 is not established by the reviewed standard UG rule. Do not extend that rule just because the course level is UG.'
                    if 'published_duration' in facts:row['proposed']['published_duration']=facts['published_duration']
        except (KeyError,ValueError,TypeError) as exc:
            row.update(status='evidence_review_required',reason=str(exc));row['proposed']={}
        output.append(row)
    return output,dict(Counter(r['status'] for r in output))

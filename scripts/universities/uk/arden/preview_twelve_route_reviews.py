"""Read-only review of 12 unresolved Arden routes, with two explicit replacement previews."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from app.models import Course, Tuition

NUMBERS=[13,14,39,46,51,52,54,55,57,58,59,60]
IDS={f'C_UK004_{n:02d}' for n in NUMBERS}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def checked(folder,filename,digest):
    data=(folder/Path(filename).name).read_bytes()
    if hashlib.sha256(data).hexdigest()!=digest:
        raise ValueError('Evidence checksum failed: '+str(filename))
    return data


def run(evidence_path='logs/arden/pending-21-investigation-20261023T095806836678Z/pending-21-evidence.json'):
    root=Path(settings.BASE_DIR)
    path=Path(evidence_path)
    if not path.is_absolute():path=root/path
    evidence=read(path)
    report=read(root/'data/universities/uk/arden/twelve_route_findings.json')
    courses={c['course_id']:c for c in Course.objects.filter(university_id='UK004',course_id__in=IDS).values(
        'course_id','title','level','duration','location','start_month','start_year','course_url','is_active')}
    if set(courses)!=IDS:raise ValueError('Expected all 12 pending UK004 records.')
    tuitions=list(Tuition.objects.filter(course__university_id='UK004',course_id__in=IDS,residency='INTL').values())
    report.update(generated_at=datetime.now(timezone.utc).isoformat(),mode='preview',database_changes=False,replacements_accepted=False,
                  current_database_courses=list(courses.values()),current_international_tuitions=tuitions,replacement_proposals=[])
    url='https://arden.ac.uk/our-courses/undergraduate/criminology-degree/ba-hons-criminology'
    matches=[p for p in evidence['pages'] if p['source_url']==url]
    if len(matches)!=1:raise ValueError('Expected one Criminology course capture.')
    page=matches[0]
    if page['http_status']!=200 or page['redirects']:raise ValueError('Criminology needs a direct successful capture.')
    html=checked(path.parent,page['file'],page['sha256']);soup=BeautifulSoup(html,'html.parser')
    title=soup.find('input',attrs={'name':'apply_now_course'}).get('value','').strip()
    source_id=soup.find('input',attrs={'name':'fees_course_id'}).get('value')
    selector=soup.find('select',attrs={'name':'apply_now_course_study_type'})
    options={o.get('value') for o in selector.find_all('option')} if selector else set()
    expected=f'c{source_id}|tdl1668|i2027-01|m=pt'
    if title!='BA (Hons) Criminology' or expected not in options or 'Part-time: 3 years+ (Online)' not in page['text']:
        raise ValueError('Criminology title, intake or duration evidence differs.')
    captures=[r for r in evidence['fees'] if r['course_page']==url]
    if len(captures)!=2 or {r['country'] for r in captures}!={'Nigeria','Ghana'}:
        raise ValueError('Expected NG/GH Criminology quotes.')
    for fee in captures:
        raw=checked(path.parent,fee['file'],fee['sha256'])
        text=BeautifulSoup(json.loads(raw)['data'],'html.parser').get_text(' ',strip=True)
        if fee['request_parameters']['course_id']!=source_id or text!=fee['text'] or re.findall(r'Level ([456]) fee: £([\d,]+)',text)!=[('4','5,500'),('5','5,500'),('6','5,500')]:
            raise ValueError('Criminology level-fee quote differs.')
    terms=next(p for p in evidence['pages'] if p['source_url']=='https://arden.ac.uk/terms-and-conditions')
    checked(path.parent,terms['file'],terms['sha256'])
    start=terms['text'].index('Arden University Global Tiers Offer 2026 - 2027 – Online Distance Learning')
    a=terms['text'].index('Schedule 1 – Eligible Programmes',start);b=terms['text'].index('Schedule 2 –',a)
    if 'Criminology' in terms['text'][a:b] or 'Criminal Justice' in terms['text'][a:b]:
        raise ValueError('Eligible-programme list changed; scholarship scope needs fresh review.')
    report['replacement_proposals'].append(dict(course_id='C_UK004_46',kind='explicit_replacement_proposal',
        before=courses['C_UK004_46'],after=dict(title=title,location='Online',duration='3 years+ (part-time)',
            start_month='January',start_year=2027,course_url=url),
        fee_basis='per_level',fees_by_level={'4':'5500.00','5':'5500.00','6':'5500.00'},currency='GBP',
        academic_year='2026/27',academic_year_provenance='Derived from the advertised January 2027 intake, not a printed fee-year label.',
        country_scope=['NG','GH'],source_course_code=source_id,study_option=expected,
        derived_programme_total='16500.00',separately_published_total=False,
        scholarship_status='not_in_collected_global_tiers_schedule_1',scholarship_deduction_verified=False,
        scholarship_note='No Global Tiers deduction approved. Other scholarship applicability remains unconfirmed; this does not establish that no scholarship exists.',
        deposit_status='campus_policy_not_applicable_to_online',acceptance_required=True))
    # The alternative foundation route is present in the earlier complete course/fee collection.
    cp=root/'logs/arden/course-evidence-20261023T001908157007Z/course-evidence.json'
    fp=root/'logs/arden/dynamic-fees-20261023T010111265891Z/dynamic-fees.json'
    source=read(cp);fees=read(fp)
    url='https://arden.ac.uk/our-courses/undergraduate/foundation-year-entry-degrees/bsc-hons-accounting-and-finance-foundation'
    page=next(p for p in source['pages'] if p['source_url']==url)
    html=checked(cp.parent,page['html_file'],page['sha256']);soup=BeautifulSoup(html,'html.parser')
    title=soup.find('input',attrs={'name':'apply_now_course'}).get('value','').strip()
    if title!='BSc (Hons) Accounting and Finance with Foundation Year' or 'Full-time: 4 years (Blended)' not in page['text']:
        raise ValueError('Alternative Accounting foundation route differs.')
    captures=[r for r in fees['responses'] if r['course_page']==url]
    if len(captures)!=2 or {r['country'] for r in captures}!={'Nigeria','Ghana'}:raise ValueError('Expected both foundation quotes.')
    for fee in captures:
        raw=checked(fp.parent,fee['raw_response_file'],fee['response_sha256'])
        text=BeautifulSoup(json.loads(raw)['data'],'html.parser').get_text(' ',strip=True)
        if 'Course fees are not available yet.' not in text:raise ValueError('Foundation fee availability changed; review before importing.')
    report['replacement_proposals'].append(dict(course_id='C_UK004_57',kind='explicit_replacement_proposal',
        before=courses['C_UK004_57'],after=dict(title=title,duration='4 Years',course_url=url),
        fee_available=False,verified_international_tuition=None,
        fee_note='The current BSc foundation route is advertised, but the collected international fee response is unavailable. No non-foundation fee is substituted.',
        fee_capture_provenance=str(fp),scholarship_status='pending_international_fee_intake_and_scope',scholarship_deduction_verified=False,
        deposit_status='pending_international_route_confirmation',acceptance_required=True))
    for finding in report['findings']:
        print(finding['course_id']+': '+finding['outcome'])
    output=root/'logs/arden'/('twelve-route-review-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Reviewed records: 12')
    print('Explicit replacement proposals: 2; one with a supported fee schedule, one route-only with fee unavailable.')
    print('Report:',output)
    print('Preview only. No replacements accepted; no database records or scholarship deductions changed.')
    return output

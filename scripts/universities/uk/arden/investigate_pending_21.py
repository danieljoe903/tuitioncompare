"""Collect fresh public evidence and current DB references for Arden's 21 pending fees.

Read-only: no model saves, updates, course moves, applications or emails.
Run from Django shell: from ...investigate_pending_21 import run; run()
"""
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from app.models import Course, Tuition, TuitionSource, ScholarshipRule, DepositPolicy

ARDEN = 'https://arden.ac.uk'
PENDING = [13,14,26,29,30,31,39,46,47,48,51,52,54,55,57,58,59,60,61,62,63]
IDS = [f'C_UK004_{n:02d}' for n in PENDING]
REFERENCES = ['C_ARD004_01','C_UK004_17','C_UK004_18','C_UK004_20','C_UK004_25']
FIELDS = ['course_id','university_id','title','level','duration','location','start_month','start_year','intake_months','course_url','ucas_cao_code','is_active']
SEEDS = {
13:['https://www.aru.ac.uk/study/undergraduate/agriculture'],
14:['https://www.aru.ac.uk/study/college-courses/subject-areas/veterinary-nursing'],
26:[ARDEN+'/our-courses/undergraduate/business-degree/ba-hons-business-management-top-up'],
29:[ARDEN+'/our-courses/undergraduate/business-degree/ba-hons-business-management-foundation-year'],
30:[ARDEN+'/our-courses/undergraduate/foundation-year-entry-degrees/llb-hons-qualifying-status-foundation'],
31:[ARDEN+'/our-courses/undergraduate/foundation-year-entry-degrees/ba-hons-criminology-and-psychology-foundation'],
39:[ARDEN+'/our-courses/postgraduate/masters/msc-data-analytics-project-management',ARDEN+'/our-courses/postgraduate/masters/msc-data-analytics-and-project-management'],
46:[ARDEN+'/which-degree-would-be-right-for-me',ARDEN+'/our-courses/undergraduate/criminology-degree/ba-hons-criminology'],
47:[ARDEN+'/our-courses/undergraduate/psychology-degrees/ba-hons-hrm-psychology1'],
48:[ARDEN+'/our-courses/undergraduate/healthcare-and-social-care/bsc-hons-health-and-care-management-foundation-degree'],
51:[ARDEN+'/our-courses/postgraduate/masters/msc-it-security-management'],
52:[ARDEN+'/our-courses/postgraduate/business-degree/msc-supply-chain-management-and-logistics'],
54:[],
55:[ARDEN+'/our-courses/postgraduate/masters/msc-data-analytics-information-systems-management',ARDEN+'/our-courses/postgraduate/masters/msc-data-analytics-and-information-systems-management'],
57:[ARDEN+'/our-courses/undergraduate/business-degree/ba-hons-business-management-accounting-finance-foundation-year'],
58:[ARDEN+'/berlin/our-courses/undergraduate/foundation-year-entry-degrees/ba-hons-business-management-marketing-foundation-year'],
59:[ARDEN+'/berlin/our-courses/undergraduate/foundation-year-entry-degrees/ba-hons-business-management-human-resource-management-foundation-year'],
60:[ARDEN+'/berlin/our-courses/undergraduate/foundation-year-entry-degrees/ba-hons-business-management-tourism-foundation-year'],
61:[ARDEN+'/our-courses/undergraduate/business-degree/fda-business-innovation'],
62:[ARDEN+'/our-courses/undergraduate/computing-degrees/fdsc-computing-digital-futures'],
63:[ARDEN+'/our-courses/undergraduate/healthcare-and-social-care/fdsc-health-and-care-management'],
}
NOTES = {
13:'The stored Writtle location points to ARU. Confirm ownership and any existing ARU counterpart; do not move the Arden record or import ARU prices automatically.',
14:'ARU Writtle advertises veterinary nursing provision. A college-level route does not verify this stored BSc qualification. Confirm both owner and award.',
26:'Compare with the already reviewed C_ARD004_01. This is a possible duplicate, not an extra course to map blindly to the same source.',
39:'Official graduation documents establish this historical award. They do not establish a current intake or a rename to MSc Project Management.',
46:'An Arden course-matchmaker page mentions BA Criminal Justice. BA Criminology is a different advertised title; any replacement requires explicit acceptance.',
47:'A direct Arden HRM and Psychology page was found outside the earlier collection. Verify its own application options and dynamic fees; do not substitute Business Management (HRM).',
51:'Official graduation documents establish MSc IT Security Management historically. A current Cyber Security route is already mapped to C_UK004_18; do not duplicate or assume equivalence.',
52:'MSc Supply Chain Management and Logistics is already represented by C_UK004_25. A similar title is not proof that the stored Global Supply Chain course is the same programme.',
54:'No exact currently advertised MA Education route was established. An Education subject label or PG Cert is insufficient to verify an MA.',
55:'Official Arden material establishes this historical award, including Berlin alumni. Confirm a current UK/online intake before assigning a fee.',
57:'Older official programme specifications establish this foundation pathway historically. Do not replace it silently with BSc Accounting and Finance with Foundation Year.',
58:'The exact themed foundation pathway found in the current catalogue is Berlin-based. That does not verify a UK campus intake, GBP fee or UK scholarship.',
59:'The exact themed foundation pathway found in the current catalogue is Berlin-based. That does not verify a UK campus intake, GBP fee or UK scholarship.',
60:'The exact themed foundation pathway found in the current catalogue is Berlin-based. That does not verify a UK campus intake, GBP fee or UK scholarship.',
}
PAGES = [
    ARDEN+'/our-courses', ARDEN+'/international-students/find-a-course', ARDEN+'/programme-specifications',
    ARDEN+'/international-students/fees-for-international-students', ARDEN+'/international-students/scholarships-funding',
    ARDEN+'/studying-with-us/fees-and-funding/all-scholarships', ARDEN+'/terms-and-conditions',
]


def normalize(text):
    text = re.sub(r'\b(?:ba|bsc|msc|ma|hons|honours|degree)\b',' ',text.casefold().replace('&',' and '))
    return ' '.join(re.sub(r'[^a-z0-9]+',' ',text).split())


def run(max_requests=90):
    base = Path(settings.BASE_DIR)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = base/'logs/arden'/('pending-21-investigation-'+stamp)
    folder.mkdir(parents=True, exist_ok=False)
    courses = list(Course.objects.filter(university_id='UK004',course_id__in=IDS).order_by('course_id').values(*FIELDS))
    if {c['course_id'] for c in courses} != set(IDS):
        raise ValueError('Expected all 21 pending course IDs; no collection started.')
    report = dict(generated_at=datetime.now(timezone.utc).isoformat(),university_id='UK004',database_changes=False,mappings_approved=False,
        courses=courses,
        tuitions=list(Tuition.objects.filter(course__university_id='UK004',course_id__in=IDS,residency='INTL').values()),
        sources=list(TuitionSource.objects.filter(tuition__course__university_id='UK004',tuition__course_id__in=IDS,tuition__residency='INTL').values()),
        reference_courses=list(Course.objects.filter(university_id='UK004',course_id__in=REFERENCES).values(*FIELDS)),
        reference_tuitions=list(Tuition.objects.filter(course__university_id='UK004',course_id__in=REFERENCES,residency='INTL').values()),
        reference_sources=list(TuitionSource.objects.filter(tuition__course__university_id='UK004',tuition__course_id__in=REFERENCES,tuition__residency='INTL').values()),
        aru_ownership_candidates=list(Course.objects.filter(Q(title__icontains='agriculture')|Q(title__icontains='veterinary nursing'),university_id='UK003').values(*FIELDS)),
        scholarship_rules=list(ScholarshipRule.objects.filter(scholarship__university_id='UK004').values()),
        deposit_policies=list(DepositPolicy.objects.filter(university_id='UK004').values()),
        pages=[],fees=[],reviews=[],errors=[],notes=[
            'All page routes are investigation candidates; none is an approved mapping.',
            'Historical awards, subject labels, redirects and related programmes do not confirm a current intake.',
            'Berlin pages do not verify UK study; ARU pages do not verify Arden ownership.',
            'No scholarship is deducted without a confirmed fee basis, scope and individual eligibility.',
        ])
    session = requests.Session()
    session.headers.update({'User-Agent':'Academy tuition evidence review (public pages only)'})
    cache = {}; requests_made = 0
    allowed = {'arden.ac.uk','www.arden.ac.uk','www.aru.ac.uk','aru.ac.uk'}

    def checkpoint():
        output=folder/'pending-21-evidence.json'
        output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        return output

    def fetch(url):
        nonlocal requests_made
        if url in cache:return cache[url]
        if requests_made >= max_requests:
            raise ValueError('Request limit reached; partial evidence has been saved.')
        if urlsplit(url).scheme!='https' or urlsplit(url).hostname not in allowed:
            raise ValueError('Only the configured official public domains may be fetched.')
        # Redirects are inspected before following. No application forms are submitted.
        current=url; redirects=[]
        for _ in range(5):
            if requests_made >= max_requests:
                raise ValueError('Request limit reached; partial evidence has been saved.')
            requests_made+=1
            response=session.get(current,timeout=(15,40),allow_redirects=False)
            if response.status_code in {301,302,303,307,308}:
                following=urljoin(current,response.headers.get('Location',''))
                if following==current or urlsplit(following).scheme!='https' or urlsplit(following).hostname not in allowed:
                    raise ValueError('Unreviewed redirect destination: '+following)
                redirects.append(dict(from_url=current,to_url=following,status=response.status_code));current=following
                continue
            break
        else:raise ValueError('Too many redirects.')
        number=len(cache)+1
        ext='.json' if 'json' in response.headers.get('Content-Type','') else '.html'
        filename=f'capture-{number:03d}'+ext
        data=response.content;(folder/filename).write_bytes(data)
        item=dict(requested_url=url,source_url=current,redirects=redirects,http_status=response.status_code,
                  content_type=response.headers.get('Content-Type',''),sha256=hashlib.sha256(data).hexdigest(),file=filename,
                  fetched_at=datetime.now(timezone.utc).isoformat())
        cache[url]=(item,data);return item,data

    def collect_page(url):
        if any(p['requested_url']==url for p in report['pages']):
            return next(p for p in report['pages'] if p['requested_url']==url)
        print('Reading:',url,flush=True)
        try:
            item,data=fetch(url)
            soup=BeautifulSoup(data,'html.parser')
            text=soup.get_text(' ',strip=True)
            page={**item,'heading':soup.h1.get_text(' ',strip=True) if soup.h1 else '', 'text':text,
                  'links':[dict(text=a.get_text(' ',strip=True),href=urljoin(item['source_url'],a['href'])) for a in soup.find_all('a',href=True)],
                  'tables':[[[cell.get_text(' ',strip=True) for cell in tr.find_all(['th','td'])] for tr in table.find_all('tr')] for table in soup.find_all('table')],
                  'fee_selector':{},'study_options':[],
                  'source_region':'Berlin' if '/berlin/' in urlsplit(item['source_url']).path else 'ARU' if 'aru.ac.uk' in item['source_url'] else 'Arden UK/online candidate'}
            for name in ['fees_course_id','fees_award_id','fees_awarding_body','apply_now_course']:
                field=soup.find('input',attrs={'name':name})
                if field:page['fee_selector'][name]=field.get('value','')
            selector=soup.find('select',attrs={'name':'apply_now_course_study_type'})
            if selector:page['study_options']=[dict(value=o.get('value',''),text=o.get_text(' ',strip=True)) for o in selector.find_all('option') if o.get('value')]
            country=soup.find('select',attrs={'name':'fees_country_id'})
            page['fee_country_options']={o.get_text(' ',strip=True):o.get('value','') for o in country.find_all('option')} if country else {}
            report['pages'].append(page)
            if item['http_status']!=200:report['errors'].append(dict(url=url,reason='HTTP '+str(item['http_status'])))
            checkpoint();return page
        except (requests.RequestException,ValueError,OSError) as error:
            report['errors'].append(dict(url=url,reason=str(error)));checkpoint();return None

    def collect_fees(page):
        if page['http_status']!=200 or page['source_region']!='Arden UK/online candidate':return
        selectors=page['fee_selector']
        if not all(selectors.get(key) for key in ['fees_course_id','fees_award_id','fees_awarding_body']):return
        for country in ['Nigeria','Ghana']:
            country_id=page['fee_country_options'].get(country)
            if not country_id:
                report['errors'].append(dict(url=page['source_url'],reason='Country selector missing: '+country));continue
            if any(r['course_page']==page['source_url'] and r['country']==country for r in report['fees']):continue
            params=dict(country_id=country_id,award_id=selectors['fees_award_id'],awarding_body=selectors['fees_awarding_body'],course_id=selectors['fees_course_id'])
            url=requests.Request('GET',ARDEN+'/fees/course-page',params=params).prepare().url
            print('Fee:',page['heading'],'/',country,flush=True)
            try:
                capture,data=fetch(url)
                if capture['http_status']!=200:raise ValueError('Fee endpoint HTTP '+str(capture['http_status']))
                payload=json.loads(data);fragment=payload.get('data')
                if not isinstance(fragment,str):raise ValueError('Fee endpoint did not return the expected HTML fragment.')
                soup=BeautifulSoup(fragment,'html.parser');text=soup.get_text(' ',strip=True)
                available='not available yet' not in text.lower() and bool(re.search(r'[£€]\s*[0-9]',text))
                report['fees'].append({**capture,'course_page':page['source_url'],'country':country,'request_parameters':params,
                    'text':text,'amounts_present':available,'intake_applicability_verified':False,'mapping_approved':False,
                    'warning':'A displayed amount alone does not approve a route, intake, currency or scholarship deduction.'})
                checkpoint()
            except (requests.RequestException,ValueError,OSError) as error:
                report['errors'].append(dict(url=url,reason=str(error)));checkpoint()

    try:
        for url in PAGES:collect_page(url)
        catalogue_links=[link for page in report['pages'] if page['requested_url'].endswith(('/our-courses','/programme-specifications')) for link in page['links']]
        for course in courses:
            cid=course['course_id'];number=int(cid.rsplit('_',1)[1]);urls=list(SEEDS[number])
            exact_links=[l for l in catalogue_links if normalize(re.split(r'view course',l['text'],maxsplit=1,flags=re.I)[0])==normalize(course['title']) and '/our-courses/' in l['href'] and urlsplit(l['href']).hostname in allowed]
            urls=list(dict.fromkeys([*urls,*[l['href'] for l in exact_links]]))[:5]
            pages=[]
            for url in urls:
                page=collect_page(url)
                if page:pages.append(page);collect_fees(page)
            direct=[p for p in pages if p['http_status']==200 and not p['redirects'] and '/our-courses/' in p['source_url'] and p['source_region']=='Arden UK/online candidate' and normalize(p['fee_selector'].get('apply_now_course',''))==normalize(course['title'])]
            if number in {13,14}:status='university_ownership_review'
            elif number==26:status='duplicate_candidate_review'
            elif direct:status='direct_route_found_fee_and_intake_review'
            elif any(p['source_region']=='Berlin' for p in pages):status='berlin_route_does_not_confirm_uk_intake'
            else:status='current_exact_route_or_intake_unconfirmed'
            review=dict(course_id=cid,stored_title=course['title'],status=status,candidate_urls=urls,
                        direct_route_candidates=[p['source_url'] for p in direct],
                        notes=NOTES.get(number,'Recheck the captured international fee response; no general undergraduate price should replace an unpublished foundation fee.'),
                        scholarship_status='pending_confirmed_route_fee_basis_and_published_award_scope',scholarship_deduction_applied=False,
                        deposit_status='pending_route_confirmation',mappings_approved=False)
            if number==26:
                canonical=next((c for c in report['reference_courses'] if c['course_id']=='C_ARD004_01'),None)
                review['canonical_candidate']='C_ARD004_01'
                review['normalized_title_matches_canonical']=bool(canonical and normalize(canonical['title'])==normalize(course['title']))
            report['reviews'].append(review);checkpoint()
            print(cid+': '+status,flush=True)
    finally:
        session.close()
        report['summary']=dict(database_courses=len(courses),reviews_completed=len(report['reviews']),pages_collected=len(report['pages']),fee_responses=len(report['fees']),
            collection_issues=len(report['errors']),request_attempts=requests_made,status_counts=dict(Counter(r['status'] for r in report['reviews'])))
        output=checkpoint()
    archive=Path(shutil.make_archive(str(folder)+'-review','zip',root_dir=folder))
    print(json.dumps(report['summary'],indent=2))
    print('Report:',output)
    print('Attach:',archive)
    print('Evidence and current records collected only. No database records changed; no mappings, deductions, retirements or course transfers applied.')
    return output

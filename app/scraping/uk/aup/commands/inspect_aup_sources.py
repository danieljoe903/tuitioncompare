"""Collect official AUP course/policy evidence. No approved mappings or DB writes."""
import hashlib
import io
import json
import re
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from app.models import Course, Tuition, TuitionSource, Scholarship, ScholarshipRule, DepositPolicy

SEEDS=[
 'https://www.aup.ac.uk/courses',
 'https://www.aup.ac.uk/international/finance',
 'https://www.aup.ac.uk/study/undergraduate/fees-financing-and-funding',
 'https://www.aup.ac.uk/study/postgraduate/fees-finance-funding',
 'https://www.aup.ac.uk/study/undergraduate',
 'https://www.aup.ac.uk/study/postgraduate',
 'https://www.aup.ac.uk/international/how-to-apply',
]



def official(url):
    p=urlsplit(url)
    return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and (p.hostname=='aup.ac.uk' or (p.hostname or '').endswith('.aup.ac.uk'))


def clean_url(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme,p.netloc,p.path.rstrip('/') or '/',p.query,''))


def title_key(text):
    text=text.casefold().replace('&',' and ')
    text=re.sub(r'\blaunching\s+(?:sept(?:ember)?\s+)?20\d{2}.*$', '', text)
    text=re.sub(r'\bhons\b',' ',text)
    return re.sub(r'[^a-z0-9]+',' ',text).strip()



def links_from(soup,base):
    links=[];seen=set()
    for a in soup.select('a[href]'):
        url=clean_url(urljoin(base,a['href']))
        label=' '.join(a.get_text(' ',strip=True).split())
        if official(url) and (url,label) not in seen:
            seen.add((url,label));links.append(dict(text=label,url=url))
    return links


def course_link(link):
    label=link['text'].strip()
    path=urlsplit(link['url']).path
    return bool(re.match(r'^(BA\b|BSc\b|MA\b|MFA\b|CertHE\b|Integrated Foundation)',label,re.I)) and path.startswith('/courses/')


def policy_link(link):
    text=(link['text']+' '+link['url']).casefold()
    return any(w in text for w in ['fees-financ','fees-finance','international/finance','scholarship','student-contract','student contract','terms-and-conditions','terms and conditions','international/how-to-apply'])



def fetch(session,url):
    redirects=[]
    for _ in range(6):
        if not official(url):raise ValueError('Off-domain URL stopped: '+url)
        response=session.get(url,timeout=(15,40),allow_redirects=False,stream=True)
        if response.status_code in {301,302,303,307,308}:
            target=urljoin(url,response.headers.get('Location',''))
            redirects.append(dict(from_url=url,to_url=target,status=response.status_code));response.close()
            if not target or not official(target):raise ValueError('External or invalid redirect stopped: '+target)
            url=target;continue
        chunks=[];size=0
        for chunk in response.iter_content(65536):
            size+=len(chunk)
            if size>12*1024*1024:
                response.close();raise ValueError('Source exceeds 12 MiB evidence limit.')
            chunks.append(chunk)
        content=b''.join(chunks)
        result=(response.status_code,url,dict(response.headers),content,redirects)
        response.close();return result
    raise ValueError('Too many source redirects.')


def candidates(course,catalogue):
    target=title_key(course['title']);found=[]
    for link in catalogue:
        key=title_key(link['text'])
        if not key:continue
        # Preserve award-level differences rather than promoting a related subject.
        if course['level']=='PG' and not re.match(r'^(ma|mfa)\b',key):continue
        if course['level'] in ('UG','TOPUP') and re.match(r'^(ma|mfa)\b',key):continue
        score=SequenceMatcher(None,target,key).ratio()
        if score<0.64:continue
        found.append(dict(source_title=link['text'],source_url=link['url'],score=round(score,3),
            status='advanced_entry_requires_review' if course['level']=='TOPUP' else 'exact_title_candidate' if key==target else 'title_or_award_review',
            mapping_approved=False))
    unique={}
    for row in sorted(found,key=lambda x:x['score'],reverse=True):unique.setdefault(row['source_url'],row)
    return list(unique.values())[:4]


class Command(BaseCommand):
    help='Collect AUP course/fee/scholarship/deposit evidence for all database courses. No mappings approved or database changes.'

    def add_arguments(self,parser):
        parser.add_argument('--max-pages',type=int,default=95,help='Maximum official pages/PDFs to collect (default 95).')

    def handle(self,*args,**options):
        limit=options['max_pages']
        if limit<7 or limit>200:raise CommandError('--max-pages must be between 7 and 200.')
        courses=list(Course.objects.filter(university_id='UK006').order_by('course_id').values())
        if not courses:raise CommandError('No UK006 course records found.')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        folder=Path(settings.BASE_DIR)/'logs/aup'/('source-review-'+stamp)
        folder.mkdir(parents=True,exist_ok=False)
        snapshot=dict(courses=courses,
            tuitions=list(Tuition.objects.filter(course__university_id='UK006').values()),
            sources=list(TuitionSource.objects.filter(tuition__course__university_id='UK006').values()),
            scholarships=list(Scholarship.objects.filter(university_id='UK006').values()),
            scholarship_rules=list(ScholarshipRule.objects.filter(scholarship__university_id='UK006').values()),
            deposit_policies=list(DepositPolicy.objects.filter(university_id='UK006').values()))
        existing_award_source_issues=[dict(scholarship_id=a['id'],name=a['name'],source_url=a.get('url'),
            reason='Stored scholarship URL is outside AUP; verify university ownership, cohort and actual terms before any deduction.')
            for a in snapshot['scholarships'] if not official(a.get('url') or '')]
        queue=list(SEEDS);seen=set();pages=[];errors=[];reviews=[];catalogue=[]
        for c in courses:
            url=c.get('course_url') or ''
            if official(url) and urlsplit(url).path not in ('','/'):queue.append(clean_url(url))
        session=requests.Session()
        session.headers['User-Agent']='UniversityTuitionCompare evidence review/1.0'
        retry=Retry(total=2,backoff_factor=0.5,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])
        session.mount('https://',HTTPAdapter(max_retries=retry))
        try:
            while queue and len(seen)<limit:
                requested=queue.pop(0)
                if requested in seen:continue
                seen.add(requested);self.stdout.write('Reading: '+requested)
                try:
                    status,url,headers,content,redirects=fetch(session,requested)
                    pdf='application/pdf' in headers.get('Content-Type','').lower() or content.startswith(b'%PDF')
                    file='page-'+str(len(pages)+1).zfill(3)+('.pdf' if pdf else '.html')
                    (folder/file).write_bytes(content)
                    row=dict(requested_url=requested,source_url=url,http_status=status,redirects=redirects,
                             fetched_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(content).hexdigest(),file=file,content_type=headers.get('Content-Type',''))
                    if pdf:
                        try:
                            from pypdf import PdfReader
                            row['text']='\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(content)).pages)
                            row['text_extraction']='pypdf'
                        except ImportError:
                            row['text']='';row['text_extraction']='not_available';reviews.append(dict(source_url=url,reason='PDF saved; text extraction requires pypdf or manual review.'))
                        row.update(heading='PDF policy document',links=[],tables=[])
                    else:
                        soup=BeautifulSoup(content,'html.parser')
                        links=links_from(soup,url)
                        main=soup.find('main') or soup.body or soup
                        heading=main.find('h1') or soup.find('h1')
                        row.update(heading=heading.get_text(' ',strip=True) if heading else '',
                                   text=' '.join(main.get_text(' ',strip=True).split()),links=links,
                                   tables=[[[cell.get_text(' ',strip=True) for cell in tr.find_all(['th','td'])] for tr in table.find_all('tr')] for table in main.find_all('table')])
                        # Decode literal rich HTML text separately, retaining original extraction.
                        import html
                        row['raw_extracted_text']=row['text']
                        if '&lt;' in row['text'] or re.search(r'<(?:p|ul|li|strong|table)\b',row['text']):
                            decoded=BeautifulSoup(html.unescape(row['text']),'html.parser')
                            row['text']=' '.join(decoded.get_text(' ',strip=True).split())
                            row['rich_html_decoded']=True
                        row['structured_data']=[]
                        for script in soup.find_all('script',type=re.compile(r'application/(?:ld\+json|json)',re.I)):
                            try:row['structured_data'].append(json.loads(script.string or script.get_text()))
                            except (TypeError,ValueError):pass
                        row['application_links']=[dict(text=a.get_text(' ',strip=True),url=urljoin(url,a['href']))
                            for a in soup.select('a[href]') if any(w in (a.get('href','')+' '+a.get_text(' ',strip=True)).casefold()
                            for w in ['ucas.com','digital.ucas.com','apply now'])]
                        # Navigation course links are evidence candidates, not approved identities.
                        for link in links:
                            if course_link(link):
                                catalogue.append(link)
                                if link['url'] not in seen and link['url'] not in queue:queue.append(link['url'])
                            elif policy_link(link) and link['url'] not in seen and link['url'] not in queue:
                                queue.insert(0,link['url'])
                    row['cohort_labels']=sorted(set(re.findall(r'20\d{2}\s*[/–-]\s*(?:20)?\d{2}',row['text'])))
                    pages.append(row)
                    if status>=400:errors.append(dict(source_url=url,status=status,reason='HTTP error; capture saved. Not evidence of course closure.'))
                except Exception as exc:errors.append(dict(source_url=requested,reason=type(exc).__name__+': '+str(exc)))
        finally:session.close()
        catalogue=list({(r['url'],r['text']):r for r in catalogue}.values())
        results=[dict(database_course=c,candidates=candidates(c,catalogue),mapping_approved=False,review_required=True) for c in courses]
        summary=dict(database_courses=len(courses),international_tuition_rows=sum(t['residency']=='INTL' for t in snapshot['tuitions']),
                     pages_collected=len(pages),catalogue_course_links=len(catalogue),courses_with_candidates=sum(bool(c['candidates']) for c in results),
                     courses_without_candidates=sum(not c['candidates'] for c in results),collection_errors=len(errors),documents_needing_review=len(reviews),
                     queued_pages_not_collected=len(set(queue)-seen),stored_awards_with_source_issues=len(existing_award_source_issues))
        report=dict(generated_at=datetime.now(timezone.utc).isoformat(),university_id='UK006',database_changes=False,mappings_approved=False,
                    existing_award_source_issues=existing_award_source_issues,summary=summary,courses=results,database_snapshot=snapshot,pages=pages,errors=errors,document_reviews=reviews,
                    notes=['Candidate similarity is not a verified mapping. Check full BA/BSc/MA award, campus, duration, mode and intake.',
                           'A standard BA page does not establish a separately advertised top-up; advanced entry must be reviewed.',
                           'Read exact fee cohort and qualification: BA/MA and CertHE prices may differ; do not import a search snippet.',
                           'Scholarship maximums are not guaranteed awards. Published application deadlines may refer to older cohorts; do not roll them forward.',
                           'Acceptance/registration payments and pre-CAS payments may be different stages; do not sum without evidence.',
                           'No tuition, scholarship, deposit or course updates are applied by this collector. Existing legacy fields are not verified by export.'])
        path=folder/'source-review.json'
        path.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        self.stdout.write(json.dumps(summary,indent=2));self.stdout.write('Report: '+str(path))
        self.stdout.write('Evidence collected only. No mappings approved. No database records changed.')
        if errors:self.stdout.write(self.style.WARNING('Some sources need review; details and captured responses were saved.'))

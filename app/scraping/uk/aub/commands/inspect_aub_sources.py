"""Collect official AUB course/policy evidence. No approved mappings or DB writes."""
import hashlib
import io
import json
import re
import shutil
from copy import deepcopy
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
 'https://aub.ac.uk/courses',
 'https://aub.ac.uk/fees/undergraduate',
 'https://aub.ac.uk/fees/postgraduate',
 'https://aub.ac.uk/fees/postgraduate-online',
 'https://aub.ac.uk/integrated-foundation-year',
 'https://aub.ac.uk/international/international-scholarships',
 'https://aub.ac.uk/international/international-scholarships/international-undergraduate-and-postgraduate-scholarships',
 'https://webdocs.aub.ac.uk/Fees%20Payment%20Policy.pdf',
]


def official(url):
    p=urlsplit(url)
    return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and (p.hostname=='aub.ac.uk' or (p.hostname or '').endswith('.aub.ac.uk'))


def clean_url(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme,p.netloc,p.path.rstrip('/') or '/',p.query,''))


def title_key(text):
    text=text.casefold().replace('&',' and ')
    text=re.sub(r'\b(?:hons|riba|arb)\b',' ',text)
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
    return official(link['url']) and bool(re.match(r'^(BA\b|BSc\b|BA/BSc\b|MA\b|Master of Architecture\b|Integrated Foundation Year\b)',label,re.I))


def policy_link(link):
    text=(link['text']+' '+link['url']).casefold()
    return any(w in text for w in ['fees payment','fees-payment','payment%20policy','payment policy','international-scholarship','scholarship-terms','fees/postgraduate','fees/undergraduate','cas-deposit'])


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
    title=course['title'];foundation='with foundation year' in title.casefold()
    target=title_key(title.replace('with Foundation Year',''))
    found=[]
    for link in catalogue:
        label=link['text'];key=title_key(label)
        if not key:continue
        score=SequenceMatcher(None,target,key).ratio()
        if score<0.64:continue
        found.append(dict(source_title=label,source_url=link['url'],score=round(score,3),
                          status='integrated_foundation_route_review' if foundation else 'exact_title_candidate' if key==target else 'title_or_award_review',
                          mapping_approved=False,foundation_year_not_implied_by_standard_route=foundation))
    if foundation:
        found.append(dict(source_title='Integrated Foundation Year',source_url='https://aub.ac.uk/integrated-foundation-year',score=1.0,status='shared_foundation_page_requires_subject_confirmation',mapping_approved=False,foundation_year_not_implied_by_standard_route=True))
    unique={}
    for row in sorted(found,key=lambda x:x['score'],reverse=True):
        unique.setdefault(row['source_url'],row)
    return list(unique.values())[:4]


class Command(BaseCommand):
    help='Collect AUB course/fee/scholarship/deposit evidence for all database courses. No mappings approved or database changes.'

    def add_arguments(self,parser):
        parser.add_argument('--reuse-evidence',help='Reuse an earlier source-review.json and verified raw captures; collect only missing URLs.')
        parser.add_argument('--max-pages',type=int,default=95,help='Maximum official pages/PDFs to collect (default 95).')

    def handle(self,*args,**options):
        limit=options['max_pages']
        if limit<8 or limit>200:raise CommandError('--max-pages must be between 8 and 200.')
        courses=list(Course.objects.filter(university_id='UK005').order_by('course_id').values())
        if not courses:raise CommandError('No UK005 course records found.')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        folder=Path(settings.BASE_DIR)/'logs/aub'/('source-review-'+stamp)
        folder.mkdir(parents=True,exist_ok=False)
        snapshot=dict(courses=courses,
            tuitions=list(Tuition.objects.filter(course__university_id='UK005').values()),
            sources=list(TuitionSource.objects.filter(tuition__course__university_id='UK005').values()),
            scholarships=list(Scholarship.objects.filter(university_id='UK005').values()),
            scholarship_rules=list(ScholarshipRule.objects.filter(scholarship__university_id='UK005').values()),
            deposit_policies=list(DepositPolicy.objects.filter(university_id='UK005').values()))
        queue=list(SEEDS);seen=set();pages=[];errors=[];reviews=[];catalogue=[]
        for c in courses:
            url=c.get('course_url') or ''
            if official(url) and urlsplit(url).path not in ('','/'):queue.append(clean_url(url))
        reused_pages=0
        reuse=options.get('reuse_evidence')
        if reuse:
            old_path=Path(reuse)
            if not old_path.is_absolute():old_path=Path(settings.BASE_DIR)/old_path
            old=json.loads(old_path.read_text(encoding='utf-8'))
            if old.get('university_id')!='UK005':raise CommandError('Reuse evidence must belong to AUB UK005.')
            for prior in old.get('pages',[]):
                filename=prior.get('file','')
                if not filename or Path(filename).name!=filename:raise CommandError('Invalid captured file name in evidence.')
                capture=old_path.parent/filename
                if not capture.is_file():raise CommandError('Keep the raw evidence files beside the JSON: missing '+str(capture))
                content=capture.read_bytes()
                if hashlib.sha256(content).hexdigest()!=prior.get('sha256'):raise CommandError('Capture hash mismatch: '+filename)
                shutil.copy2(capture,folder/filename)
                row=deepcopy(prior)
                if filename.endswith('.pdf'):
                    try:
                        from pypdf import PdfReader
                        row['text']='\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(content)).pages)
                        row['text_extraction']='pypdf'
                        row['cohort_labels']=sorted(set(re.findall(r'20\d{2}\s*[/–-]\s*(?:20)?\d{2}',row['text'])))
                    except ImportError:
                        reviews.append(dict(source_url=row['source_url'],reason='PDF retained; install pypdf for text extraction or review manually.'))
                pages.append(row)
                seen.update([row['requested_url'],row['source_url']])
                for link in row.get('links',[]):
                    if course_link(link):
                        catalogue.append(link)
                        if link['url'] not in queue:queue.append(link['url'])
                    elif policy_link(link) and official(link['url']) and link['url'] not in queue:
                        queue.append(link['url'])
            reused_pages=len(pages)
            self.stdout.write('Verified and reused captures: '+str(reused_pages))
        session=requests.Session()
        session.headers['User-Agent']='UniversityTuitionCompare evidence review/1.0'
        retry=Retry(total=2,backoff_factor=0.5,status_forcelist=[429,500,502,503,504],allowed_methods=['GET'])
        session.mount('https://',HTTPAdapter(max_retries=retry))
        try:
            while queue and len(pages)<limit:
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
                     pages_collected=len(pages),pages_reused=reused_pages,new_pages_collected=len(pages)-reused_pages,catalogue_course_links=len(catalogue),courses_with_candidates=sum(bool(c['candidates']) for c in results),
                     courses_without_candidates=sum(not c['candidates'] for c in results),collection_errors=len(errors),documents_needing_review=len(reviews),
                     queued_pages_not_collected=len(set(queue)-seen))
        report=dict(generated_at=datetime.now(timezone.utc).isoformat(),university_id='UK005',database_changes=False,mappings_approved=False,
                    reused_evidence=str(old_path) if reuse else None,summary=summary,courses=results,database_snapshot=snapshot,pages=pages,errors=errors,document_reviews=reviews,
                    notes=['Candidate similarity is not a verified mapping. Check full BA/BSc/MA award, campus, duration, mode and intake.',
                           'Standard degree pages do not automatically verify integrated foundation-year eligibility.',
                           'Read fee cohort from the table row, not the introductory paragraph.',
                           'Scholarship maximums are not guaranteed awards; unknown eligibility blocks deductions.',
                           'Acceptance/registration payments and pre-CAS payments may be different stages; do not sum without evidence.',
                           'No tuition, scholarship, deposit or course updates are applied by this collector.'])
        path=folder/'source-review.json'
        path.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        self.stdout.write(json.dumps(summary,indent=2));self.stdout.write('Report: '+str(path))
        self.stdout.write('Evidence collected only. No mappings approved. No database records changed.')
        if errors:self.stdout.write(self.style.WARNING('Some sources need review; details and captured responses were saved.'))

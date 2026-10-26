"""Archive specific UCAS routes and preview every remaining AUB record; no writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from app.models import Course,Tuition,TuitionSource
from app.scraping.uk.aub.remaining_review import build_review,PUBLIC_REFERENCE_SHA256

DEFAULT='logs/aub/source-review-20261024T004000918202Z/source-review.json'


def collect(url,folder,index):
    record={'requested_url':url,'source_url':url,'fetched_at':datetime.now(timezone.utc).isoformat()}
    try:
        response=requests.get(url,headers={'User-Agent':'UniversityTuitionCompare/1.0 (course evidence review)'},timeout=(15,35))
        record.update(source_url=response.url,http_status=response.status_code)
        if urlparse(response.url).hostname not in {'www.ucas.com','ucas.com'}:raise ValueError('Unexpected redirect host; manual source review required.')
        filename=f'ucas-{index:02d}.html';(folder/filename).write_bytes(response.content)
        record.update(file=filename,sha256=hashlib.sha256(response.content).hexdigest())
        response.raise_for_status()
        soup=BeautifulSoup(response.content,'html.parser');heading=soup.find('h1')
        for tag in soup(['script','style','noscript']):tag.decompose()
        record.update(heading=heading.get_text(' ',strip=True) if heading else '',text=' '.join(soup.get_text(' ',strip=True).split()))
    except (requests.RequestException,ValueError) as exc:record['error']=str(exc)
    return url,record


class Command(BaseCommand):
    help='Read-only review of all twenty remaining AUB records with exact UCAS route captures; no mappings or fees applied.'
    def add_arguments(self,parser):
        parser.add_argument('--evidence',default=DEFAULT)
        parser.add_argument('--public-references',action='store_true',help='Use the included checked public-listing references instead of requesting UCAS pages. Source review is labelled separately from raw HTML captures.')
        parser.add_argument('--reuse-review',help='Reuse the exact captured UCAS pages in a prior remaining-20 review; checks raw hashes. No new requests.')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);evidence=Path(options['evidence'])
        if not evidence.is_absolute():evidence=root/evidence
        data=json.loads(evidence.read_text(encoding='utf-8'))
        if data.get('university_id')!='UK005':raise CommandError('Expected AUB evidence.')
        for p in data['pages']:
            if Path(p['file']).name!=p['file']:raise CommandError('Invalid source capture filename.')
            if hashlib.sha256((evidence.parent/p['file']).read_bytes()).hexdigest()!=p['sha256']:raise CommandError('Saved source capture changed: '+p['file'])
        manifest=json.loads((root/'data/universities/uk/aub/remaining_20_review.json').read_text(encoding='utf-8'));ids=[i['course_id'] for i in manifest]
        courses={c['course_id']:c for c in Course.objects.filter(university_id='UK005',course_id__in=ids).values()}
        if set(courses)!=set(ids):raise CommandError('Expected all twenty AUB course IDs.')
        tuitions=list(Tuition.objects.filter(course__university_id='UK005',course_id__in=ids,residency='INTL').values())
        sources=list(TuitionSource.objects.filter(tuition__course__university_id='UK005',tuition__course_id__in=ids,tuition__residency='INTL').values())
        folder=root/'logs/aub'/('remaining-20-review-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
        urls=[r['ucas_url'] for r in manifest if r.get('ucas_url')]
        if options.get('public_references') and options.get('reuse_review'):raise CommandError('Choose public references or a reused capture report, not both.')
        public_reference_file=None
        if options.get('public_references'):
            reference=root/'data/universities/uk/aub/remaining_20_public_references.json'
            body=reference.read_bytes()
            if hashlib.sha256(body).hexdigest()!=PUBLIC_REFERENCE_SHA256:raise CommandError('The checked public reference file changed; review it before proceeding.')
            refdata=json.loads(body)
            if refdata.get('university_id')!='UK005' or refdata.get('schema_version')!=1:raise CommandError('Invalid checked reference set.')
            captures=refdata['references'];public_reference_file='reviewed-public-references.json'
            (folder/public_reference_file).write_bytes(body)
            self.stdout.write('Using 12 checked public-listing references. No UCAS HTTP requests; these are not local HTML captures.')
        elif options.get('reuse_review'):
            old=Path(options['reuse_review']);old=old if old.is_absolute() else root/old
            prior=json.loads(old.read_text(encoding='utf-8'));captures=prior['route_captures']
            if set(captures)!=set(urls):raise CommandError('Reused route captures do not match this reviewed list.')
            for page in captures.values():
                if page.get('file'):
                    if Path(page['file']).name!=page['file']:raise CommandError('Invalid reused capture filename.')
                    body=(old.parent/page['file']).read_bytes()
                    if hashlib.sha256(body).hexdigest()!=page['sha256']:raise CommandError('Reused UCAS capture changed.')
                    (folder/page['file']).write_bytes(body)
        else:
            self.stdout.write('Collecting twelve exact UCAS route pages; saved AUB captures are reused.')
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures=[pool.submit(collect,url,folder,i) for i,url in enumerate(urls,1)]
                captures=dict(f.result() for f in futures)
        try:rows,summary=build_review(data,manifest,courses,tuitions,captures)
        except (ValueError,KeyError) as exc:raise CommandError(str(exc)) from exc
        report={'generated_at':datetime.now(timezone.utc).isoformat(),'university_id':'UK005','database_changes':False,'mappings_approved':False,'evidence_path':str(evidence.resolve()),'saved_capture_hashes_checked':len(data['pages']),'summary':summary,'reviewed_records':len(rows),'collection_errors':sum(bool(p.get('error')) for p in captures.values()),'route_captures':{} if options.get('public_references') else captures,'reviewed_route_references':captures if options.get('public_references') else {},'public_reference_file':public_reference_file,'public_reference_sha256':PUBLIC_REFERENCE_SHA256 if options.get('public_references') else None,'evidence_mode':'reviewed_public_references' if options.get('public_references') else 'local_http_captures','reviews':rows,'current_sources':sources,'notes':['No course, tuition, scholarship rule or deposit policy has been changed.','These are proposals. Provisional fees are not final verified prices; unavailable route captures are not evidence of closure.','No maximum or hypothetical scholarship has been deducted. Questions remain for the scholarship page.','The first thirty applied routes and their saved rules remain unchanged. Final AUB handoff documentation is deferred.']}
        output=folder/'remaining-20-review.json';output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        for r in rows:
            self.stdout.write(r['course_id']+': '+r['status'])
            if r['proposed']:self.stdout.write('  '+json.dumps(r['proposed'],ensure_ascii=False))
            self.stdout.write('  '+r['reason'])
        self.stdout.write('Reviewed records: '+str(len(rows)))
        for k,v in summary.items():self.stdout.write(k+': '+str(v))
        self.stdout.write('Collection errors: '+str(report['collection_errors']))
        self.stdout.write('Report: '+str(output.resolve()))
        self.stdout.write('Preview only. No database records changed. No award granted or maximum automatically deducted.')

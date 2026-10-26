"""Preview 23 AUP routes from captured evidence plus current application cycle."""
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from bs4 import BeautifulSoup
import html
import requests
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from app.scraping.uk.aup.commands.inspect_aup_sources import fetch
from app.scraping.uk.aup.review import plan,APPLY,INTL,UG,PG

class Command(BaseCommand):
    help='Preview 23 exact-route AUP annual fees and preserve three route cases. No database updates.'
    def add_arguments(self,parser):parser.add_argument('--evidence',help='Path to the saved AUP source-review.json; defaults to the newest captured report.')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        if options.get('evidence'):
            ep=Path(options['evidence']);ep=ep if ep.is_absolute() else root/ep
        else:
            paths=sorted((root/'logs/aup').glob('source-review-*/source-review.json'))
            if not paths:raise CommandError('Run inspect_aup_sources first.')
            ep=paths[-1]
        data=json.loads(ep.read_text(encoding='utf-8'));manifest=json.loads((root/'data/universities/uk/aup/reviewed_23_routes.json').read_text(encoding='utf-8'))
        urls={INTL,UG,PG}|{r['source_url'] for r in manifest}
        pages={p['source_url']:p for p in data['pages']}
        for url in urls:
            p=pages[url]
            if Path(p['file']).name!=p['file'] or hashlib.sha256((ep.parent/p['file']).read_bytes()).hexdigest()!=p['sha256']:raise CommandError('Source capture hash mismatch: '+url)
        folder=root/'logs/aup'/('reviewed-routes-preview-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
        try:
            with requests.Session() as session:
                status,url,headers,content,redirects=fetch(session,APPLY)
            if status!=200 or url.rstrip('/')!=APPLY:raise ValueError('Official application source unavailable or redirected.')
            (folder/'application.html').write_bytes(content)
            soup=BeautifulSoup(content,'html.parser');main=soup.find('main') or soup.body or soup
            text=' '.join(main.get_text(' ',strip=True).split())
            if '<p' in text or '&lt;' in text:text=' '.join(BeautifulSoup(html.unescape(text),'html.parser').get_text(' ',strip=True).split())
            report=plan(data,manifest,text)
        except (requests.RequestException,ValueError) as exc:raise CommandError('Preview stopped: '+str(exc)) from exc
        report.update(generated_at=datetime.now(timezone.utc).isoformat(),evidence_path=str(ep),application_capture={'source_url':APPLY,'file':'application.html','sha256':hashlib.sha256(content).hexdigest(),'text':text})
        out=folder/'preview.json';out.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        for r in report['proposals']:
            self.stdout.write(r['course_id']+': GBP '+r['tuition_fee_before']+' -> '+r['tuition_fee']+'; September 2027 / 2027/28; '+r['qualification'])
            for key,value in r['course_changes'].items():self.stdout.write('  '+key+': '+str(value['before'])+' -> '+str(value['after']))
        for r in report['pending']:self.stdout.write(r['course_id']+': pending - '+r['reason'])
        for r in report['blocked']:self.stdout.write(r['course_id']+': BLOCKED - '+r['reason'])
        self.stdout.write(json.dumps(report['summary'],indent=2));self.stdout.write('Report: '+str(out))
        self.stdout.write('Preview only. No database records changed. Scholarship and conditional deposit rules require reviewed import.')
        if report['blocked']:raise CommandError('Some route/fee proposals need review; report saved.')

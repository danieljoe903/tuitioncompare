"""Capture governing policies and save exact-cohort rules atomically."""
import hashlib,io,json,re
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.utils.timezone import now
from app.models import Tuition,TuitionSource,Scholarship,ScholarshipRule,DepositPolicy
from app.scraping.uk.aston.calculation import all_fee_manifest as manifest,fee_error
from app.scraping.uk.aston.awards import OWNER,WEB,FUNDING,SPONSOR,UG_PDF,JAN_PDF,SEP_PDF,CAD_PDF,specs,make_rule,deposit_rule,notices
from app.scraping.uk.aston.commands.inspect_aston_sources import fetch

MBA_JAN='https://www.aston.ac.uk/study/courses/mba-full-time-mba/january-2027'
MBA_SEP='https://www.aston.ac.uk/study/courses/mba-full-time-mba/september-2027'
MBA_AWARDS='https://www.aston.ac.uk/aston-business-school/aston-mba/mba-scholarships'
MBA_OLD_TERMS='https://www.aston.ac.uk/sites/default/files/International%20Scholarship%20TC%2025-26.pdf'

def normalized(text):return ' '.join(text.split()).casefold()
def require(text, phrases, label):
    t=normalized(text)
    for phrase in phrases:
        if normalized(phrase) not in t:raise CommandError(label+': expected policy wording missing: '+phrase+'. No database records changed.')

def collect(folder):
    try:from pypdf import PdfReader
    except ImportError as exc:raise CommandError('Install PDF support: python -m pip install "pypdf[crypto]"') from exc
    pages={}
    with requests.Session() as session:
        session.headers['User-Agent']='UniversityTuitionCompare policy review/1.0'
        for i,url in enumerate([WEB,FUNDING,SPONSOR,UG_PDF,JAN_PDF,SEP_PDF,CAD_PDF,MBA_JAN,MBA_SEP,MBA_AWARDS,MBA_OLD_TERMS],1):
            try:status,actual,headers,raw,redirects=fetch(session,url)
            except Exception as exc:raise CommandError('Policy capture failed: '+url+' — '+str(exc)) from exc
            if status!=200 or actual!=url:raise CommandError('Unexpected policy status/redirect: '+url)
            pdf=raw.startswith(b'%PDF');name=f'policy-{i:02d}'+('.pdf' if pdf else '.html')
            (folder/name).write_bytes(raw)
            try:
                text=' '.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(raw)).pages) if pdf else BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)
            except Exception as exc:raise CommandError('PDF extraction failed. Install python -m pip install "pypdf[crypto]" and retry. '+str(exc)) from exc
            pages[url]=dict(source_url=url,file=name,sha256=hashlib.sha256(raw).hexdigest(),text=text)
    # The governing documents, not obsolete webpage application wording, set ICS conditions.
    require(pages[UG_PDF]['text'],['September 2027 intake','Version V3','13 October 2026','automatic','£9,000','Year 1: £3,000','MPharm Pharmacy','BSc Healthcare Science (Audiology)','partnership','terms and conditions shall prevail'], 'UG governing terms')
    for url,month,value in [(JAN_PDF,'January','£4,000'),(SEP_PDF,'September','£5,500')]:
        require(pages[url]['text'],[month+' 2027 intake','Birmingham','self-funded','automatic','enrolment','offer letter',value,'All MBA programmes','All MRes programmes','separate','partnership'],month+' PG terms')
    require(pages[CAD_PDF]['text'],['January 2027','September 2027','£10,000','competitive','application-based','cannot be used for deposits','MPharm Pharmacy','BSc Healthcare Science (Audiology)','supersede','successfully enrolled'],'Cadbury terms')
    require(pages[WEB]['text'],['1 November 2026','23:59','Scholarships for September 2027','Deadline to apply: TBC'],'Award dates')
    require(pages[FUNDING]['text'],['All International students','student loan','Government-sponsored students','deducted from your first-year tuition fees','offer letter'],'Deposit policy')
    require(pages[SPONSOR]['text'],['deposit payment requirement will be waived','sponsor is paying the tuition fees to the University'],'Sponsored payment assessment')
    require(pages[WEB]['text'],['Top-up BSc (Hons)','£3,000 only'],'Top-up single-payment scope')
    require(pages[CAD_PDF]['text'],['MPH Public Health'],'MPH Cadbury exclusion')
    for url,year in [(MBA_JAN,'2026/27'),(MBA_SEP,'2027/28')]:
        require(pages[url]['text'],[year,'MBA deposit payment','£5,000 deposit','deducted from your total tuition fee amount upon enrolment'],'MBA published deposit')
    require(pages[MBA_AWARDS]['text'],['MBA Scholarships 2026/2027','£12,500','£6,500','ONE scholarship'],'MBA award-cycle review')
    require(pages[MBA_OLD_TERMS]['text'],['2025/26','only'],'MBA linked terms mismatch')
    (folder/'policy-evidence.json').write_text(json.dumps(pages,indent=2,ensure_ascii=False),encoding='utf-8')
    return pages

class Command(BaseCommand):
    help='Reconcile all48 reviewed fees and extend Aston Community/Cadbury deductions and offer-letter deposit policies. Preview by default.'
    def add_arguments(self,p):p.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        if options['apply'] and not any('# Aston reviewed30 awards v1' in p.read_text(encoding='utf-8-sig') for p in (root/'app').rglob('*.py')):
            raise CommandError('Run scripts/universities/uk/aston/connect_awards.py --apply first.')
        if len(manifest())!=48:raise CommandError('Expected 48 reviewed fee routes before reconciliation.')
        folder=root/'logs/aston'/('pending-routes-awards-'+now().strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
        self.stdout.write('Collecting governing award policies, MBA deposit pages and linked MBA terms...')
        evidence=collect(folder);defined=specs(manifest());rule_plans=[];policy_plans=[]
        with transaction.atomic():
            for cid in manifest():
                rows=list(Tuition.objects.filter(course_id=cid,residency='INTL').select_related('course'))
                if len(rows)!=1:raise CommandError(cid+': expected one INTL row.')
                source=TuitionSource.objects.filter(tuition=rows[0]).first()
                error=fee_error(rows[0],source)
                if error:raise CommandError(cid+': '+error)
            for spec in defined.values():
                awards=list(Scholarship.objects.select_for_update().filter(university_id='UK007',name=spec['name']))
                if len(awards)>1:raise CommandError('Duplicate managed award: '+spec['name'])
                award=awards[0] if awards else None
                rule=ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None
                if award and (not rule or not isinstance(rule.rules,dict) or rule.rules.get('importer')!=OWNER):raise CommandError('Existing award ownership conflict.')
                desired={**make_rule(spec),'evidence_sha256':evidence[spec['source_url']]['sha256']}
                changed=rule is None or award.level!=spec['level'] or not rule.verified or rule.source_url!=spec['source_url'] or rule.rules!=desired
                rule_plans.append((spec,award,desired,changed))
            for year in ['2026/27','2027/28']:
                policies=list(DepositPolicy.objects.select_for_update().filter(university_id='UK007',academic_year=year))
                if len(policies)>1:raise CommandError('Multiple deposit policies for '+year)
                p=policies[0] if policies else None
                if p and (not isinstance(p.rules,dict) or p.rules.get('importer')!=OWNER):raise CommandError('Existing deposit policy requires review: '+year)
                desired=dict(currency='GBP',verified=True,source_url=FUNDING,rules=deposit_rule(year,manifest()))
                changed=p is None or any(getattr(p,k)!=v for k,v in desired.items())
                policy_plans.append((year,desired,changed))
            if options['apply']:
                for spec,award,desired,changed in rule_plans:
                    if not changed:continue
                    if award is None:
                        award=Scholarship.objects.create(university_id='UK007',name=spec['name'],level=spec['level'],
                            award_type='Conditional tuition reduction',award_value=spec['amount'],currency='GBP',deadline=spec['deadline'],
                            url=spec['source_url'],eligibility='Exact reviewed Birmingham programme, intake, Overseas fee status and fully self-funded applicant.',
                            requirements='Saved applicant confirmation of every governing rule condition; questions belong on scholarship page.',
                            description='Community: automatic under governing terms, not guaranteed before conditions. Cadbury: competitive and requires a confirmed award. Only one award is held; no deposit payment is inferred.')
                    elif award.level!=spec['level']:
                        award.level=spec['level'];award.save(update_fields=['level'])
                    ScholarshipRule.objects.update_or_create(scholarship=award,defaults=dict(verified=True,source_url=spec['source_url'],rules=desired,reviewed_at=now()))
                for year,desired,changed in policy_plans:
                    if changed:DepositPolicy.objects.update_or_create(university_id='UK007',academic_year=year,
                        defaults={**desired,'source_text':desired['rules']['note'],'source_sha256':evidence[FUNDING]['sha256'],'checked_at':now()})
        changed=sum(r[-1] for r in rule_plans)+sum(p[-1] for p in policy_plans)
        report=dict(university_id='UK007',generated_at=now().isoformat(),database_changes=bool(options['apply'] and changed),changed_items=changed,
            rules=[dict(name=s['name'],course_count=len(s['course_ids']),changed=ch,rules=r) for s,a,r,ch in rule_plans],
            deposit_policies=[dict(academic_year=y,changed=ch,rules=p['rules']) for y,p,ch in policy_plans],
            excluded_courses=['C_UK007_17','C_UK007_24','C_UK007_31','C_UK007_32','C_UK007_33','C_UK007_50'],notices=notices(),
            discrepancy='UG webpage describes competitive applications; governing V3 PDF (13 October 2026) states automatic and takes precedence.',
            batch_review={'topup':'Single conditional GBP 3,000 payment; exact Top-up entry and non-partner confirmation required; Cadbury not extended.', 'mph':'Conditional GBP 5,500 Community for full-time September 2027; Cadbury excluded.', 'advanced_computer_science':'January 2027 Community GBP 4,000 or confirmed Cadbury GBP 10,000, never combined.', 'mba_scholarships':'Both MBA intakes excluded from Community/Cadbury. MBA marketing 2026/27 links to 2025/26 terms; exact-intake award remains pending.', 'mba_deposits':'Published GBP 5,000 acceptance deposit on each exact-year course page, included in tuition. Offer/waiver confirmation required for numeric calculation.'},
            legacy_review='Two stored Global Excellence awards have an old 14 June 2026 deadline and no verified current rule; they remain excluded, not renamed or rolled into 2027.')
        output=folder/'reconciliation.json';output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        self.stdout.write('Community scopes: 19 UG September (GBP 3,000 first year); 19 PG January (GBP 4,000); 2 PG September plus MPH (GBP 5,500); one Top-up (GBP 3,000 once).')
        self.stdout.write('Cadbury GBP 10,000: 40 conditional course scopes; confirmed competitive award required; never added to Community award.')
        self.stdout.write('Medicine, Optometry, Pharmacy, Audiology and MBA: excluded from both reviewed awards. Deposit policies: 48 routes across two years. MBA GBP 5,000 acceptance deposit published; exact offer/waiver confirmation required. Other amounts remain offer-letter specific.')
        self.stdout.write('MBA scholarship deduction pending: 2026/27 marketing links to 2025/26 terms; September 2027 award not confirmed. No historic award rolled forward.')
        self.stdout.write('Changed/proposed items: '+str(changed));self.stdout.write('Report: '+str(output))
        self.stdout.write('Saved rules and policies. Gross tuition and stored numeric discounts/deposits unchanged.' if options['apply'] else 'Preview only. No database records changed.')

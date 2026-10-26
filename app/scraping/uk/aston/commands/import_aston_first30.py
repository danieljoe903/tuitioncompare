from decimal import Decimal
from pathlib import Path
import hashlib,json
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import Course,Tuition,TuitionSource,TuitionUpdateAudit
from app.scraping.uk.aston.first30 import plan
from app.scraping.uk.aston.calculation import OWNER

def canon(value):return json.loads(json.dumps(value,cls=DjangoJSONEncoder,sort_keys=True))
def money(value):return str(Decimal(str(value or 0)).quantize(Decimal('.01')))

def duration_for_field(c,published):
    limit=c._meta.get_field('duration').max_length
    if not limit or len(published)<=limit:return published
    if published.startswith('4 years with integrated placement'):short='4 Years (placement optional)'
    elif published=='One year':short='1 Year'
    else:short=published
    if len(short)>limit:short='4 Years' if published.startswith('4 years with integrated placement') else short
    if len(short)>limit:raise CommandError('Duration field too short; full published wording retained in source but a reviewed short label is needed.')
    return short

class Command(BaseCommand):
    help='Preview/apply 30 source-backed Aston fees and route metadata; no scholarship/deposit policy approval.'
    def add_arguments(self,p):
        p.add_argument('--preview');p.add_argument('--apply',action='store_true')
        p.add_argument('--accept-course-corrections',action='store_true')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        if options['apply'] and not options['accept_course_corrections']:
            raise CommandError('Use --apply --accept-course-corrections to save reviewed duration/intake corrections.')
        if options['apply'] and not any('# Aston first30 comparison v1' in p.read_text(encoding='utf-8-sig') for p in (root/'app').rglob('*.py')):
            raise CommandError('Run scripts/universities/uk/aston/connect_comparison.py --apply first.')
        if options.get('preview'):
            pp=Path(options['preview']);pp=pp if pp.is_absolute() else root/pp
        else:
            files=sorted((root/'logs/aston').glob('first30-preview-*/preview.json'))
            if not files:raise CommandError('Run preview_aston_first30 first.')
            pp=files[-1]
        supplied=json.loads(pp.read_text(encoding='utf-8-sig'))
        ep=Path(supplied['evidence_path']);ep=ep if ep.is_absolute() else root/ep
        data=json.loads(ep.read_text(encoding='utf-8-sig'))
        manifest=json.loads((root/'data/universities/uk/aston/reviewed_first30.json').read_text())
        rebuilt=plan(data,manifest)
        if supplied.get('university_id')!='UK007' or rebuilt['blocked'] or len(rebuilt['proposals'])!=30 or canon(rebuilt['proposals'])!=canon(supplied['proposals']):
            raise CommandError('Preview differs from the reviewed plan; nothing applied.')
        op=Path(data['original_evidence_path']);op=op if op.is_absolute() else root/op
        pages={p['source_url']:p for p in data['pages']}
        for m in manifest:
            p=pages[m['source_url']]
            if Path(p['file']).name!=p['file'] or hashlib.sha256((op.parent/p['file']).read_bytes()).hexdigest()!=m['source_sha256']:
                raise CommandError('Raw source hash mismatch: '+m['course_id'])
        report=dict(university_id='UK007',database_changes=False,generated_at=now().isoformat(),courses=[])
        with transaction.atomic():
            plans=[]
            for row in rebuilt['proposals']:
                cid=row['course_id'];c=Course.objects.select_for_update().get(university_id='UK007',course_id=cid)
                ts=list(Tuition.objects.select_for_update().filter(course=c,residency='INTL'))
                if len(ts)!=1:raise CommandError(cid+': expected one INTL row.')
                t=ts[0];sources=list(TuitionSource.objects.select_for_update().filter(tuition=t))
                if len(sources)>1:raise CommandError(cid+': multiple sources require review.')
                source=sources[0] if sources else None
                owned=source is not None and isinstance(source.raw_fees,dict) and source.raw_fees.get('importer')==OWNER
                if source and not owned:raise CommandError(cid+': an existing source belongs to another workflow.')
                desired={**row['proposed_course'],'duration':duration_for_field(c,row['published_duration']), 'intake_months':row['start_month']}
                keys=set(desired)|{'title','level','location','is_active'}
                before_c={k:getattr(c,k) for k in keys}
                expected={**{k:row['before_course'][k] for k in keys},**(desired if owned else {})}
                if before_c!=expected:raise CommandError(cid+': stored course differs from reviewed before/after state.')
                old=row['before_international_tuition']
                if t.currency!='GBP' or money(t.tuition_fee)!=money(row['tuition_fee'] if owned else old['tuition_fee']):raise CommandError(cid+': stored tuition changed since preview.')
                if money(t.scholarship)!=('0.00' if owned else money(old['scholarship'])) or t.scholarship_source_id!=(None if owned else old['scholarship_source_id']):raise CommandError(cid+': stored discount changed; do not overwrite.')
                raw=dict(importer=OWNER,verified=True,academic_year=row['academic_year'],fee_basis=row['fee_basis'],
                    source_sha256=row['source_sha256'],currency='GBP',tuition_fee=row['tuition_fee'],
                    source_course_name=c.title,source_course_code=desired.get('ucas_cao_code',c.ucas_cao_code),
                    published_duration=row['published_duration'],stored_duration=desired['duration'],stored_location=c.location,
                    fee_note=row['fee_note'],scholarship_review=row['scholarship_status'],deposit_review=row['deposit_status'],
                    course_kind='postgraduate_taught' if c.level=='PG' else 'undergraduate_taught')
                defaults=dict(academic_year=row['academic_year'],source_url=row['source_url'],fee_basis=row['fee_basis'],raw_fees=raw)
                before=dict(course=before_c,tuition_fee=money(t.tuition_fee),scholarship=money(t.scholarship),scholarship_source_id=t.scholarship_source_id,
                    source={k:getattr(source,k) for k in defaults} if source else None)
                after=dict(course={**before_c,**desired},tuition_fee=row['tuition_fee'],scholarship='0.00',scholarship_source_id=None,source=defaults)
                changed=canon(before)!=canon(after)
                plans.append((c,t,desired,row,defaults,before,after,changed))
                report['courses'].append(dict(course_id=cid,changed=changed,before=canon(before),after=canon(after)))
            if options['apply']:
                for c,t,desired,row,defaults,before,after,changed in plans:
                    if not changed:continue
                    for k,v in desired.items():setattr(c,k,v)
                    c.save(update_fields=[*desired,'updated_at'])
                    t.tuition_fee=Decimal(row['tuition_fee']);t.scholarship=0;t.scholarship_source_id=None
                    t.save(update_fields=['tuition_fee','scholarship','scholarship_source'])
                    TuitionSource.objects.update_or_create(tuition=t,defaults={**defaults,'checked_at':now()})
                    TuitionUpdateAudit.objects.create(tuition=t,course_identifier=c.course_id,residency='INTL',before=canon(before),after=canon(after))
            report['changed_courses']=sum(p[-1] for p in plans)
            report['database_changes']=bool(options['apply'] and report['changed_courses'])
            folder=root/'logs/aston'/('first30-import-'+now().strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
            output=folder/'reconciliation.json';output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2),encoding='utf-8')
        self.stdout.write('Changed/proposed courses: '+str(report['changed_courses']))
        self.stdout.write('30 fee routes; published duration wording retained in source; short stored labels used only where field length requires.')
        self.stdout.write('Scholarship/deposit reconciliation pending. Legacy discounts cleared with audit on apply; numeric deposits and EU fees preserved.')
        self.stdout.write('Report: '+str(output));self.stdout.write('Applied.' if options['apply'] else 'Preview only. No database records changed.')

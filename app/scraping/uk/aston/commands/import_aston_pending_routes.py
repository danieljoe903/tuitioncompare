"""Atomic import of the five reviewed pending-route changes."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import Course, Tuition, TuitionSource, TuitionUpdateAudit
from app.scraping.uk.aston.commands.preview_aston_pending_routes import build_report, SPECS
OWNER = 'aston_pending_routes_v1'
def canon(v): return json.loads(json.dumps(v, cls=DjangoJSONEncoder, sort_keys=True))
def money(v): return str(Decimal(str(v or 0)).quantize(Decimal('.01')))
def resolve(root, p):
    p = Path(p)
    return p if p.is_absolute() else root / p

def comparable_proposals(rows):
    # Collector serializers use ISO milliseconds; live values use datetime
    # microseconds. Creation/update timestamps are not part of the fee plan.
    result = canon(rows)
    for row in result:
        course = row.get('before_course', {})
        course.pop('created_at', None)
        course.pop('updated_at', None)
    return result

def read_plan(root, pp):
    supplied = json.loads(pp.read_text(encoding='utf-8-sig'))
    ep = resolve(root, supplied['evidence_path'])
    data = json.loads(ep.read_text(encoding='utf-8-sig'))
    rebuilt = build_report(data, ep, root,
        {c['course_id']:c for c in data['database_courses']}, data['international_tuitions'])
    if supplied.get('university_id') != 'UK007' or rebuilt['blocked'] or len(rebuilt['proposals']) != 5 or comparable_proposals(rebuilt['proposals']) != comparable_proposals(supplied.get('proposals', [])):
        raise CommandError('Preview differs from the source-backed reviewed plan. Run the preview again.')
    pinned = json.loads((root/'data/universities/uk/aston/reviewed_pending_routes.json').read_text())
    for m in pinned:
        row = next((p for p in rebuilt['proposals'] if p['course_id']==m['course_id']), None)
        if row is None or any(row[k]!=m[k] for k in ('source_url','source_sha256','tuition_fee','academic_year','fee_basis')):
            raise CommandError('Pinned source evidence differs; review required.')
    return rebuilt['proposals']

class Command(BaseCommand):
    help = 'Preview/apply four fee routes and the distinct September MBA intake correction; scholarship/deposit scopes unchanged.'
    def add_arguments(self, p):
        p.add_argument('--preview'); p.add_argument('--apply', action='store_true')
        p.add_argument('--accept-course-corrections', action='store_true')
        p.add_argument('--accept-replacements', action='store_true')
    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        if options['apply'] and not (options['accept_course_corrections'] and options['accept_replacements']):
            raise CommandError('Apply requires --accept-course-corrections --accept-replacements.')
        if options.get('preview'): pp = resolve(root, options['preview'])
        else:
            paths = sorted((root/'logs/aston').glob('pending-routes-preview-*/preview.json'))
            if not paths: raise CommandError('Run preview_aston_pending_routes first.')
            pp = paths[-1]
        try: rows = read_plan(root, pp)
        except (OSError, KeyError, ValueError) as exc: raise CommandError(str(exc)) from exc
        report = dict(university_id='UK007', generated_at=now().isoformat(), preview_path=str(pp), courses=[])
        with transaction.atomic():
            for row in rows:
                cid = row['course_id']
                c = Course.objects.select_for_update().get(course_id=cid, university_id='UK007')
                ts = list(Tuition.objects.select_for_update().filter(course=c,residency='INTL'))
                if len(ts)>1: raise CommandError(cid+': multiple INTL rows.')
                t = ts[0] if ts else None
                ss = list(TuitionSource.objects.select_for_update().filter(tuition=t)) if t else []
                if len(ss)>1: raise CommandError(cid+': multiple sources.')
                source = ss[0] if ss else None
                owned = source is not None and source.raw_fees.get('importer') == OWNER
                if source and not owned and not (cid=='C_UK007_24' and source.raw_fees.get('importer')=='aston_remaining14_v1'):
                    raise CommandError(cid+': existing source needs ownership review.')
                desired = row['proposed_course']
                keys = set(desired)|{'title','level','location','is_active'}
                before_c = {k:getattr(c,k) for k in keys}
                original_c = {k:row['before_course'][k] for k in keys}
                expected_c = {**original_c,**(desired if owned else {})}
                if before_c != expected_c: raise CommandError(cid+': course changed since preview.')
                old = row['before_international_tuition']
                if not t and old is not None: raise CommandError(cid+': tuition row disappeared.')
                if t:
                    if not owned and old is None: raise CommandError(cid+': unexpected existing tuition row.')
                    if t.currency!='GBP' or money(t.tuition_fee)!=money(row['tuition_fee'] if owned else old['tuition_fee']):
                        raise CommandError(cid+': tuition changed since review.')
                    if money(t.scholarship)!=('0.00' if owned else money(old['scholarship'])) or t.scholarship_source_id!=(None if owned else old['scholarship_source_id']):
                        raise CommandError(cid+': scholarship changed since review.')
                raw = dict(importer=OWNER, verified=True, academic_year=row['academic_year'],fee_basis=row['fee_basis'],
                    currency='GBP',tuition_fee=row['tuition_fee'],source_sha256=row['source_sha256'],
                    source_course_name=desired.get('title',c.title),source_course_code=desired.get('ucas_cao_code',c.ucas_cao_code),
                    published_duration='One year FT / Two years PT' if cid=='C_UK007_26' else '1 year' if cid=='C_AST007_01' else 'One year',
                    stored_duration=desired['duration'],stored_location=c.location,fee_note=row['fee_note'],
                    course_kind='postgraduate_taught' if c.level=='PG' else 'undergraduate_taught',
                    scholarship_review='Exact route/intake reconciliation pending; no new deduction verified.',
                    deposit_review=row['deposit_note'],applications_opening_soon=row['applications_opening_soon'],
                    replacement_history=dict(previous_title=original_c['title'],replacement_title=desired['title'],accepted=True,mapping='explicit_replacement') if row['requires_accept_replacements'] else None)
                defaults = dict(academic_year=row['academic_year'],source_url=row['source_url'],fee_basis=row['fee_basis'],raw_fees=raw)
                before = dict(course=before_c, tuition_fee=money(t.tuition_fee) if t else None,
                    scholarship=money(t.scholarship) if t else None,scholarship_source_id=t.scholarship_source_id if t else None,
                    source={k:getattr(source,k) for k in defaults} if source else None)
                after = dict(course={**before_c,**desired},tuition_fee=row['tuition_fee'],scholarship='0.00',scholarship_source_id=None,source=defaults)
                changed = canon(before)!=canon(after)
                if options['apply'] and changed:
                    for k,v in desired.items(): setattr(c,k,v)
                    c.save(update_fields=[*desired,'updated_at'])
                    if t is None:
                        t = Tuition.objects.create(course=c,residency='INTL',currency='GBP',tuition_fee=Decimal(row['tuition_fee']),scholarship=0,scholarship_source=None)
                    else:
                        t.tuition_fee=Decimal(row['tuition_fee']);t.scholarship=0;t.scholarship_source_id=None
                        t.save(update_fields=['tuition_fee','scholarship','scholarship_source'])
                    TuitionSource.objects.update_or_create(tuition=t,defaults={**defaults,'checked_at':now()})
                    TuitionUpdateAudit.objects.create(tuition=t,course_identifier=cid,residency='INTL',before=canon(before),after=canon(after))
                report['courses'].append(dict(course_id=cid,changed=changed,before=canon(before),after=canon(after)))
            report['changed_courses']=sum(r['changed'] for r in report['courses'])
            report['database_changes']=bool(options['apply'] and report['changed_courses'])
            folder=root/'logs/aston'/('pending-routes-import-'+now().strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
            output=folder/'reconciliation.json';output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2),encoding='utf-8')
        self.stdout.write('Changed/proposed course records: '+str(report['changed_courses']))
        self.stdout.write('Four fee routes and one existing MBA intake correction; seven unresolved routes untouched.')
        self.stdout.write('Scholarship/deposit reconciliation pending. EU fees and existing numeric deposits preserved; legacy discounts cleared with audit.')
        self.stdout.write('Report: '+str(output));self.stdout.write('Applied.' if options['apply'] else 'Preview only. No database records changed.')

"""Preview/apply the reviewed first 30 AUB campus routes, fees and award rules."""
import ast
from copy import deepcopy
from decimal import Decimal
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import Course,Tuition,TuitionSource,TuitionUpdateAudit,Scholarship,ScholarshipRule
from app.scraping.uk.aub.calculation import OWNER,AWARD_OWNER,FACTS,QUESTIONS
from app.scraping.uk.aub.commands.preview_aub_first_30 import plan_preview,AWARDS,UG,PG

DEFAULT='logs/aub/first-30-preview-20261024T004839623164Z.json'


def canonical(x):return json.loads(json.dumps(x,cls=DjangoJSONEncoder,sort_keys=True))


def source_state(source):
    if source is None:return None
    return canonical({k:getattr(source,k) for k in ['academic_year','source_url','source_course_code','source_course_name','fee_basis','raw_fees']})


def duration_evidence(course,page):
    text=page['text'];notes='The stored duration has not been independently confirmed by a course specification in this batch.';verified=False
    match=re.search(r'Duration:\s*(.*?)(?:Placement year:|Placement:|Course code:|Institution code:|Overview)',text)
    published=match.group(1).strip() if match else None
    if course.level=='UG' and published and re.match(r'3(?:\s|–|-| or)',published):
        verified=True;notes='Reviewed standard three-year route. Published duration is '+published+'. Optional foundation/placement routes are separate; none is selected by this import.'
    elif course.course_id=='C_UK005_22' and 'Two years. Full-time.' in text:
        verified=True;notes='Published two-year full-time MArch route.';published='2 Years, full-time'
    return dict(duration_verified=verified,published_duration=published,duration_note=notes)


def course_state(course):
    return {k:getattr(course,k) for k in ['course_id','title','level','duration','start_month','start_year','course_url','ucas_cao_code','intake_months','location','is_active']}


def tuition_state(t):
    return dict(tuition_fee=str(Decimal(str(t.tuition_fee)).quantize(Decimal('.01'))),scholarship=str(Decimal(str(t.scholarship or 0)).quantize(Decimal('.01'))),scholarship_source_id=t.scholarship_source_id,tuition_deposit=str(t.tuition_deposit),currency=t.currency)


def rules_for(level,ids,sha):
    maximum='5000.00' if level=='UG' else '3000.00'
    conditions={k:[True] for k in FACTS}
    conditions.update(course_id=ids,fee_status=['INTL'],aub_award_amount_valid=[True])
    return dict(importer=AWARD_OWNER,kind='confirmed_amount',value=maximum,published_maximum=maximum,
        confirmed_amount_fact='aub_confirmed_award_amount',benefit='tuition',currency='GBP',
        academic_year='2027/28',study_years=[1],conditions=conditions,award_status='competitive',
        stacking='exclusive',compatible_ids=[],percentage_basis='original_tuition',fee_basis_confirmed=True,
        source_capture_sha256=sha,eligibility_questions=QUESTIONS,questions_location='scholarship_page',
        scope_note='Exact reviewed campus course IDs and compared year only. Deduct the actual granted amount, bounded by the published maximum, once saved personal facts are confirmed. The AUB adapter is required; the generic calculator cannot interpret confirmed_amount.')


class Command(BaseCommand):
    help='Preview/apply 30 reviewed AUB campus routes, 2027/28 tuition sources and competitive actual-amount rules.'
    def add_arguments(self,parser):
        parser.add_argument('--preview',default=DEFAULT)
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--accept-course-corrections',action='store_true')
    def handle(self,*args,**options):
        apply=options['apply'];root=Path(settings.BASE_DIR)
        if apply and not options['accept_course_corrections']:raise CommandError('Apply requires --accept-course-corrections for the reviewed September 2027 route/intake changes.')
        if apply:
            connected=False
            for p in (root/'app').rglob('*.py'):
                text=p.read_text(encoding='utf-8-sig')
                if '# AUB reviewed campus comparison v1' in text and 'def compare_tuition(' in text:connected=True;break
            if not connected:raise CommandError('Connect the comparison adapter before applying this batch.')
        preview_path=Path(options['preview'])
        if not preview_path.is_absolute():preview_path=root/preview_path
        preview=json.loads(preview_path.read_text(encoding='utf-8'))
        manifest=json.loads((root/'data/universities/uk/aub/first_30_reviewed_routes.json').read_text(encoding='utf-8'))
        ids=[r['course_id'] for r in manifest]
        if {r['course_id'] for r in preview['proposals']}!=set(ids) or preview.get('university_id')!='UK005':raise CommandError('Expected the reviewed 30-course AUB preview.')
        # The user-generated evidence path may be absolute on Windows. Use the
        # local logs path when the project has been copied to another machine.
        evidence_path=Path(preview['evidence_path'])
        if not evidence_path.is_file():
            name=preview['evidence_path'].replace('\\','/').split('/')[-2]
            evidence_path=root/'logs/aub'/name/'source-review.json'
        data=json.loads(evidence_path.read_text(encoding='utf-8'))
        for p in data['pages']:
            if Path(p['file']).name!=p['file']:raise CommandError('Invalid captured evidence name.')
            if hashlib.sha256((evidence_path.parent/p['file']).read_bytes()).hexdigest()!=p['sha256']:raise CommandError('Evidence hash mismatch: '+p['file'])
        pages={p['source_url']:p for p in data['pages']}
        snapshot={r['course_id']:r for r in preview['proposals']}
        report=dict(generated_at=now().isoformat(),university_id='UK005',mode='apply' if apply else 'preview',database_changes=False,courses=[],awards=[])
        with transaction.atomic():
            courses=list(Course.objects.select_for_update().filter(university_id='UK005',course_id__in=ids))
            if {c.course_id for c in courses}!=set(ids):raise CommandError('Missing reviewed courses.')
            fresh=plan_preview(data,manifest,{c.course_id:course_state(c) for c in courses})
            proposals={p['course_id']:p for p in fresh};plans=[]
            for c in courses:
                cid=c.course_id;p=proposals[cid];old=snapshot[cid]['before'];ts=list(Tuition.objects.select_for_update().filter(course_id=cid,residency='INTL'))
                if len(ts)!=1:raise CommandError(cid+': expected one INTL tuition.')
                tuition=ts[0];source=TuitionSource.objects.select_for_update().filter(tuition=tuition).first()
                owned=source is not None and isinstance(source.raw_fees,dict) and source.raw_fees.get('importer')==OWNER
                if source is not None and not owned:raise CommandError(cid+': existing source is outside this importer; review it before overwrite.')
                if Decimal(str(tuition.tuition_fee))!=Decimal('19950.00') or tuition.currency!='GBP':raise CommandError(cid+': stored fee/currency changed.')
                if not owned:
                    if c.start_year!=old['start_year'] or c.start_month!=old['start_month'] or c.course_url!=old['course_url']:raise CommandError(cid+': course changed since preview.')
                    if tuition.scholarship_source_id is not None or Decimal(str(tuition.scholarship or 0))!=Decimal(str(old['tuition']['scholarship'])):raise CommandError(cid+': discount/link changed since preview; do not clear without review.')
                page=pages[p['evidence']['course_page']];duration=duration_evidence(c,page)
                after_course=course_state(c)
                after_course.update(start_year=2027,start_month='September',intake_months='September',course_url=p['proposed']['course_url'])
                if p['proposed']['ucas_code_if_captured']:after_course['ucas_cao_code']=p['proposed']['ucas_code_if_captured']
                # Retain the standard route duration; only alter an objectively
                # mismatched stored duration where this capture establishes it.
                if duration['duration_verified']:after_course['duration']='2 Years' if cid=='C_UK005_22' else '3 Years'
                raw=dict(importer=OWNER,verified=True,academic_year='2027/28',currency='GBP',source_fee_category='International',
                    annual_tuition='19950.00',study_year=1,study_mode='campus',default_intake='2027-09',
                    course_kind='undergraduate_taught' if c.level=='UG' else 'postgraduate_taught',
                    source_course_url=p['proposed']['course_url'],source_sha256=page['sha256'],
                    fee_source_sha256=pages[p['evidence']['fee_page']]['sha256'],subject_to_validation=p['subject_to_validation'],
                    fee_note='Published international tuition for the reviewed September 2027 campus route and 2027/28 fee period. '+('Course is subject to validation. ' if p['subject_to_validation'] else '')+duration['duration_note'],
                    scholarship_review=dict(status='competitive_actual_award_amount_required',reason='AUB award maximum is not automatic; use the actual granted amount saved from the scholarship page.',eligibility_questions=QUESTIONS),
                    deposit_status='2027_28_policy_confirmation_required',deposit_review=p['deposit'],mapping=dict(kind='reviewed_same_award_route',accepted_intake='2027-09'),**duration)
                defaults=dict(academic_year='2027/28',source_url=p['evidence']['fee_page'],source_course_code=p['proposed']['ucas_code_if_captured'] or '',source_course_name=page['heading'],fee_basis='annual_tuition',raw_fees=raw)
                before=dict(course=course_state(c),tuition=tuition_state(tuition),source=source_state(source))
                after_tuition=tuition_state(tuition);after_tuition.update(scholarship='0.00',scholarship_source_id=None)
                after=canonical(dict(course=after_course,tuition=after_tuition,source=defaults))
                changed=canonical(before)!=after
                plans.append((c,tuition,source,after_course,defaults,before,after,changed))
                report['courses'].append(dict(course_id=cid,changed=changed,before=before,after=after))
            award_plans=[]
            for level in ['UG','PG']:
                name='AUB International Competitive '+level+' — Confirmed Amount (2027/28)'
                found=list(Scholarship.objects.select_for_update().filter(university_id='UK005',name=name))
                if len(found)>1:raise CommandError('Duplicate managed scholarship name.')
                award=found[0] if found else None
                rule=ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None
                if award and (not rule or not isinstance(rule.rules,dict) or rule.rules.get('importer')!=AWARD_OWNER):raise CommandError('Existing award is outside this correction.')
                rules=rules_for(level,sorted(c.course_id for c in courses if c.level==level),pages[AWARDS]['sha256'])
                changed=rule is None or rule.rules!=rules or not rule.verified or rule.source_url!=AWARDS
                award_plans.append((level,name,award,rules,changed))
                report['awards'].append(dict(name=name,changed=changed,rules=rules))
            changed=sum(p[-1] for p in plans)+sum(p[-1] for p in award_plans)
            report.update(changed_course_records=sum(p[-1] for p in plans),changed_items=changed,database_changes=bool(apply and changed))
            output=root/'logs/aub'/('first-30-import-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
            if apply:
                for level,name,award,rules,changed_rule in award_plans:
                    if not changed_rule:continue
                    if award is None:award=Scholarship.objects.create(university_id='UK005',name=name,level=level,currency='GBP',award_type='Competitive actual award',award_value=rules['published_maximum'],deadline=None,url=AWARDS,eligibility='Reviewed overseas-fee campus offer for 2027/28; actual award must be granted and accepted.',requirements='All saved personal conditions and exact actual award amount required.',description='Published maximum only. Deduct the actual confirmed amount using the AUB adapter; no stacking.')
                    ScholarshipRule.objects.update_or_create(scholarship=award,defaults=dict(verified=True,source_url=AWARDS,rules=rules,reviewed_at=now()))
                for c,t,source,after_course,defaults,before,after,changed_course in plans:
                    if not changed_course:continue
                    fields=[]
                    for k in ['duration','start_month','start_year','course_url','ucas_cao_code','intake_months']:
                        if getattr(c,k)!=after_course[k]:setattr(c,k,after_course[k]);fields.append(k)
                    if fields:c.save(update_fields=[*fields,'updated_at'])
                    t.scholarship=0;t.scholarship_source_id=None;t.save(update_fields=['scholarship','scholarship_source'])
                    TuitionSource.objects.update_or_create(tuition=t,defaults={**defaults,'checked_at':now()})
                    TuitionUpdateAudit.objects.create(tuition=t,course_identifier=c.course_id,residency='INTL',before=canonical(before),after=after)
        self.stdout.write('Changed/proposed course records: '+str(report['changed_course_records']))
        self.stdout.write('Changed/proposed total items: '+str(changed));self.stdout.write('Report: '+str(output))
        self.stdout.write('Applied reviewed routes, intake, sources and actual-amount award rules.' if apply else 'Preview only. No database records changed.')
        self.stdout.write('Unverified stored discounts are cleared on apply; original values retained in audits. Numeric deposits and EU fees preserved; no unverified deposit displayed.')

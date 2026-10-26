"""Confirm eight Year-0 fees and the exact Dance route; other route gaps retained."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import Course,Tuition,TuitionSource,TuitionUpdateAudit,Scholarship,ScholarshipRule
from app.scraping.uk.aub.calculation import OWNER as CAMPUS_OWNER
from app.scraping.uk.aub.remaining_calculation import OWNER,FOUNDATION_IDS
from app.scraping.uk.aub.commands.import_aub_first_30 import canonical,course_state,tuition_state,source_state,rules_for,AWARDS

CONFIRM='aub_foundation_fees_v1'
UG='https://aub.ac.uk/fees/undergraduate'
DANCE='C_UK005_11'
SHA='0ab9ef0e070e05c25ef65a9a3de98605014cfd2deb8599647a3322a440c83d43'
NAME='AUB International Competitive UG — Dance Confirmed Amount (2027/28)'


def verify_table(page):
    found=[]
    for table in page['tables']:
        pairs=[(r[0].strip(),r[1].replace(',','').replace('£','').strip()) for r in table if len(r)>1]
        if ('2027/28 Applicants (all years)','19950') in pairs and ('Integrated Foundation Year – all courses','19950') in pairs:found.append(table)
    if len(found)!=1:raise ValueError('Cannot isolate the 2027/28 international table and its integrated foundation row together.')
    return found[0]


class Command(BaseCommand):
    help='Preview/apply direct AUB confirmation of eight integrated-foundation fees and the exact September 2027 Dance route.'
    def add_arguments(self,parser):
        parser.add_argument('--evidence',default='logs/aub/source-review-20261024T004000918202Z/source-review.json')
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);apply=options['apply']
        if apply and '# Preserve verified AUB foundation fee confirmation v1.' not in (root/'app/scraping/uk/aub/commands/import_aub_remaining_routes.py').read_text(encoding='utf-8-sig'):raise CommandError('Run connect_foundation_fees.py --apply before applying.')
        ep=Path(options['evidence']);ep=ep if ep.is_absolute() else root/ep;data=json.loads(ep.read_text(encoding='utf-8'));pages={p['source_url']:p for p in data['pages']}
        if data.get('university_id')!='UK005':raise CommandError('Expected AUB evidence.')
        for url in [UG,AWARDS]:
            p=pages[url]
            if Path(p['file']).name!=p['file'] or hashlib.sha256((ep.parent/p['file']).read_bytes()).hexdigest()!=p['sha256']:raise CommandError('Source capture hash mismatch.')
        try:verify_table(pages[UG])
        except ValueError as exc:raise CommandError(str(exc)) from exc
        if '2027/2028' not in pages[AWARDS]['text'] or '£5,000' not in pages[AWARDS]['text']:raise CommandError('UG award cohort/maximum changed.')
        refpath=root/'data/universities/uk/aub/dance_route_review.json';body=refpath.read_bytes()
        if hashlib.sha256(body).hexdigest()!=SHA:raise CommandError('Reviewed Dance route reference changed.')
        dance=json.loads(body);report={'university_id':'UK005','generated_at':now().isoformat(),'database_changes':False,'fee_source_url':UG,'fee_capture_sha256':pages[UG]['sha256'],'course_updates':[],'notes':['The exact 2027/28 international table includes the integrated foundation row; its older introductory year is not used to select the fee.','Foundation award Year 0 applicability and campus deposits remain pending.','Six other exact route/intake cases remain unconfirmed; no closures or replacements inferred.']}
        with transaction.atomic():
            plans=[]
            for cid in sorted(FOUNDATION_IDS|{DANCE}):
                cs=list(Course.objects.select_for_update().filter(university_id='UK005',course_id=cid));ts=list(Tuition.objects.select_for_update().filter(course_id=cid,residency='INTL'))
                if len(cs)!=1 or len(ts)!=1:raise CommandError(cid+': expected one course and international tuition.')
                c=cs[0];t=ts[0];source=TuitionSource.objects.select_for_update().filter(tuition=t).first()
                if not c.is_active or c.level!='UG' or t.currency!='GBP':raise CommandError(cid+': course/currency no longer matches reviewed scope.')
                before={'course':course_state(c),'tuition':tuition_state(t),'source':source_state(source)};after_c=course_state(c)
                if cid in FOUNDATION_IDS:
                    if not source or not isinstance(source.raw_fees,dict) or source.raw_fees.get('importer')!=OWNER or source.raw_fees.get('route_kind')!='foundation' or source.academic_year!='2027/28' or c.start_year!=2027 or c.start_month!='September':raise CommandError(cid+': expected the applied foundation route review.')
                    confirmed=source.raw_fees.get('foundation_fee_confirmation',{}).get('importer')==CONFIRM
                    if Decimal(str(t.tuition_fee)) not in ([Decimal('19950')] if confirmed else [Decimal('14750')]):raise CommandError(cid+': stored fee changed; review before overwrite.')
                    raw=deepcopy(source.raw_fees);raw.update(verified=True,annual_tuition='19950.00',fee_status='verified',fee_verified_final=True,fee_source_sha256=pages[UG]['sha256'],fee_note='Published 2027/28 international annual tuition GBP 19,950 from AUB’s integrated foundation year row. Year 0 scholarship applicability and campus deposit amount remain unconfirmed. Optional placement pricing is separate.',foundation_fee_confirmation={'importer':CONFIRM,'source_url':UG,'source_capture_sha256':pages[UG]['sha256'],'table_selector':'2027/28 Applicants (all years) plus Integrated Foundation Year – all courses; International/EU table'})
                    reason='Integrated foundation Year 0 award applicability remains unconfirmed. Foundation Diploma bursaries and progression from an already completed Foundation Diploma are separate. The UG competitive first-year wording does not explicitly confirm Year 0. Tuition verification is independent; no Year 0 deduction is established.'
                    if isinstance(raw.get('online_foundation_awards'),dict):raw['online_foundation_awards']['reason']=reason
                    if isinstance(raw.get('scholarship_review'),dict):raw['scholarship_review']['reason']=reason
                    defaults={**source_state(source),'source_url':UG,'raw_fees':raw}
                else:
                    if c.title!=dance['title']:raise CommandError('Dance title changed; no automatic mapping.')
                    if source and (not isinstance(source.raw_fees,dict) or source.raw_fees.get('dance_fee_confirmation')!=CONFIRM):raise CommandError('Dance already has unrelated source metadata; no overwrite.')
                    if not source:
                        for k,v in course_state(c).items():
                            if v!=dance['before']['course'][k]:raise CommandError('Dance record changed since reviewed snapshot: '+k)
                    if Decimal(str(t.tuition_fee))!=Decimal('19950'):raise CommandError('Dance tuition changed.')
                    after_c.update(course_url=dance['source_url'],start_month='September',start_year=2027,intake_months='September',ucas_cao_code='W713',duration='3 Years')
                    raw={'importer':CAMPUS_OWNER,'dance_fee_confirmation':CONFIRM,'verified':True,'academic_year':'2027/28','currency':'GBP','source_fee_category':'International','study_mode':'campus','course_kind':'undergraduate_taught','default_intake':'2027-09','annual_tuition':'19950.00','source_course_url':dance['source_url'],'route_evidence_kind':'reviewed_public_listing','route_reference':{k:v for k,v in dance.items() if k!='before'},'fee_source_sha256':pages[UG]['sha256'],'duration_verified':True,'duration_note':'Three-year full-time route; optional placement is separate.','subject_to_validation':None,'fee_note':'Published AUB September 2027 / 2027/28 annual international tuition for the exact Dance, Movement and Creative Practice route. UCAS code W713; three years. Deposit amount requires confirmation.','scholarship_review':{'status':'competitive_actual_amount_rule','reason':'Up to GBP 5,000 is a maximum, not an automatic award; exact granted amount and saved eligibility required.'}}
                    defaults={'academic_year':'2027/28','source_url':UG,'source_course_code':'W713','source_course_name':c.title,'fee_basis':'annual_tuition','raw_fees':raw}
                if t.scholarship_source_id is not None or (cid in FOUNDATION_IDS and Decimal(str(t.scholarship or 0))!=0):raise CommandError(cid+': unexpected stored/linked deduction; no overwrite.')
                if cid==DANCE and Decimal(str(t.scholarship or 0)) not in [Decimal('5000'),Decimal('0')]:raise CommandError('Dance stored deduction changed.')
                after_t=tuition_state(t);after_t.update(tuition_fee='19950.00',scholarship='0.00',scholarship_source_id=None);after=canonical({'course':after_c,'tuition':after_t,'source':defaults});changed=canonical(before)!=after
                plans.append((c,t,after_c,defaults,before,after,changed));report['course_updates'].append({'course_id':cid,'changed':changed,'before':before,'after':after})
            matches=list(Scholarship.objects.select_for_update().filter(university_id='UK005',name=NAME))
            if len(matches)>1:raise CommandError('Duplicate managed Dance award.')
            award=matches[0] if matches else None;rule=ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None;desired=rules_for('UG',[DANCE],pages[AWARDS]['sha256'])
            if award and (not rule or not isinstance(rule.rules,dict) or rule.rules.get('importer')!=desired['importer']):raise CommandError('Existing Dance award is not owned by this correction.')
            changed_rule=rule is None or rule.rules!=desired or not rule.verified or rule.source_url!=AWARDS
            report.update(changed_courses=sum(p[-1] for p in plans),changed_items=sum(p[-1] for p in plans)+int(changed_rule),dance_award={'changed':changed_rule,'rules':desired});report['database_changes']=bool(apply and report['changed_items'])
            output=root/'logs/aub'/('foundation-dance-fees-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json');output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
            if apply:
                if changed_rule:
                    if award is None:award=Scholarship.objects.create(university_id='UK005',name=NAME,level='UG',currency='GBP',award_type='Competitive actual award',award_value='5000.00',deadline=None,url=AWARDS,eligibility='Exact reviewed Dance course and September 2027 overseas fee offer.',requirements='Saved personal facts and actual AUB-granted scholarship amount required.',description='Published maximum only; no assumed award or stacking.')
                    ScholarshipRule.objects.update_or_create(scholarship=award,defaults={'verified':True,'source_url':AWARDS,'rules':desired,'reviewed_at':now()})
                for c,t,after_c,defaults,before,after,changed in plans:
                    if not changed:continue
                    fields=[]
                    for k,v in after_c.items():
                        if getattr(c,k)!=v:setattr(c,k,v);fields.append(k)
                    if fields:c.save(update_fields=[*fields,'updated_at'])
                    t.tuition_fee=Decimal('19950');t.scholarship=0;t.scholarship_source_id=None;t.save(update_fields=['tuition_fee','scholarship','scholarship_source'])
                    TuitionSource.objects.update_or_create(tuition=t,defaults={**defaults,'checked_at':now()})
                    TuitionUpdateAudit.objects.create(tuition=t,course_identifier=c.course_id,residency='INTL',before=canonical(before),after=after)
        self.stdout.write('Changed/proposed courses: '+str(report['changed_courses']));self.stdout.write('Changed/proposed items: '+str(report['changed_items']))
        self.stdout.write('8 foundation annual fees confirmed at GBP 19,950; Year 0 scholarship remains pending. Dance route/annual fee and actual-amount award scope reviewed.')
        self.stdout.write('6 other route/intake cases remain unconfirmed. Campus deposits require confirmation. EU fees and stored numeric deposits unchanged.')
        self.stdout.write('Report: '+str(output));self.stdout.write('Applied reviewed fees, route and Dance award rule.' if apply else 'Preview only. No database records changed.')

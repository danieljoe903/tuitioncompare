"""Preview/apply thirteen reviewed AUB routes; seven unresolved records untouched."""
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
from app.models import Course,Tuition,TuitionSource,TuitionUpdateAudit,Scholarship,ScholarshipRule,DepositPolicy
from app.scraping.uk.aub.remaining_review import build_review,PUBLIC_REFERENCE_SHA256,ONLINE_FEES,PG_FEES,AWARDS
from app.scraping.uk.aub.remaining_calculation import OWNER,ONLINE_IDS,FOUNDATION_IDS,CAMPUS_ID
from app.scraping.uk.aub.commands.import_aub_first_30 import canonical,course_state,tuition_state,source_state,rules_for

DEFAULT='logs/aub/remaining-20-review-20261024T110631590638Z/remaining-20-review.json'
AWARD_NAME='AUB International Competitive PG — Fashion Confirmed Amount (2027/28)'


def local_path(root,path):
    candidate=Path(path)
    if candidate.is_file():return candidate
    if not candidate.is_absolute() and (root/candidate).is_file():return root/candidate
    parts=str(path).replace('\\','/').split('/')
    if len(parts)>1 and (root/'logs/aub'/parts[-2]/parts[-1]).is_file():return root/'logs/aub'/parts[-2]/parts[-1]
    raise CommandError('Required saved evidence file is missing: '+str(path))


def plan_route(row,pages):
    p=row['proposed'];kind=row['category'];before=row['before'];c=dict(before['course']);t=dict(before['tuition'])
    updates={k:p[k] for k in ['course_url','duration','start_month','start_year'] if k in p};updates['intake_months']='September'
    if kind=='replacement':updates['title']=p['title'];kind='campus'
    elif kind=='online':updates['location']='Online'
    else:updates['ucas_cao_code']=p['ucas_cao_code']
    after_course={k:c[k] for k in ['course_id','title','level','duration','start_month','start_year','course_url','ucas_cao_code','intake_months','location','is_active']};after_course.update(updates)
    raw={'importer':OWNER,'verified':kind!='foundation','route_kind':kind,'study_mode':'online' if kind=='online' else 'campus','course_kind':'undergraduate_taught' if kind=='foundation' else 'postgraduate_taught','academic_year':'2027/28','currency':'GBP','source_fee_category':'International','default_intake':'2027-09','study_year':1,'source_course_url':p['course_url'],'route_evidence_kind':'reviewed_public_listing' if kind!='campus' else 'saved_direct_university_capture','route_evidence':row['evidence'],'duration_verified':p['duration_verified'],'duration_note':'Two-year part-time online route.' if kind=='online' else 'Four-year standard integrated foundation route. Optional placement may extend it.' if kind=='foundation' else 'Stored one-year duration retained; not independently established in this capture.','scholarship_review':{'status':row['scholarship']['status'],'reason':row['scholarship']['message'],'questions_location':'scholarship_page'},'deposit_review':row['deposit']}
    if kind=='campus':
        raw.update(annual_tuition='19950.00',fee_note='Published September 2027 campus MA annual international tuition. Explicit replacement from MA Digital Fashion Innovation to MA Fashion Innovation. Stored one-year duration is not independently confirmed here.',mapping={'kind':'explicit_replacement','old_title':row['title'],'new_title':p['title'],'accepted':True})
        amount='19950.00';source_url=PG_FEES
    elif kind=='online':
        raw.update(programme_total='9975.00',acceptance_payment='500.00',fee_note='Published 2027/28 two-year online programme total GBP 9,975. Country-dependent GST/VAT may apply. Non-refundable GBP 500 acceptance payment is allocated to the first tuition payment and is included in that total. UCAS generic annual labelling does not override the direct AUB total basis.',mapping={'kind':'reviewed_same_online_route'},annual_fee_available=False)
        amount='9975.00';source_url=ONLINE_FEES
    else:
        raw.update(published_provisional_fee='19950.00',fee_status='provisional',fee_verified_final=False,entry_point='Foundation',subject_to_validation=p.get('subject_to_validation'),validation_status_note=p.get('validation_status_note'),published_duration=p.get('published_duration'),fee_note='The September 2027 exact subject-specific foundation route is reviewed. Its GBP 19,950 annual international quote is provisional; final fee, Year 0 scholarship and deposit scope require confirmation. Do not display an unqualified verified amount.',mapping={'kind':'reviewed_subject_specific_foundation_route','ucas_code':p['ucas_cao_code']})
        amount=before['tuition']['tuition_fee'];source_url=p['route_source_url']
    if source_url in pages:raw['fee_source_sha256']=pages[source_url]['sha256']
    source=dict(academic_year='2027/28',source_url=source_url,source_course_code=p.get('ucas_cao_code',''),source_course_name=after_course['title'],fee_basis=p['fee_basis'],raw_fees=raw)
    return after_course,str(Decimal(str(amount)).quantize(Decimal('.01'))),source


class Command(BaseCommand):
    help='Preview/apply four online programmes, one explicit campus replacement and eight provisional foundation route corrections.'
    def add_arguments(self,parser):
        parser.add_argument('--review',default=DEFAULT)
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--accept-replacements',action='store_true')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);apply=options['apply']
        if apply and not options['accept_replacements']:raise CommandError('The Fashion replacement requires --accept-replacements.')
        if apply and not any('# AUB remaining route comparison v2' in p.read_text(encoding='utf-8-sig') for p in (root/'app').rglob('*.py')):raise CommandError('Connect the remaining-route comparison adapter before applying.')
        path=local_path(root,options['review']);review=json.loads(path.read_text(encoding='utf-8'))
        if review.get('university_id')!='UK005' or review.get('evidence_mode')!='reviewed_public_references' or review.get('public_reference_sha256')!=PUBLIC_REFERENCE_SHA256:raise CommandError('Expected the supplied checked-reference review, not a failed HTTP report.')
        refpath=root/'data/universities/uk/aub/remaining_20_public_references.json';refbody=refpath.read_bytes()
        if hashlib.sha256(refbody).hexdigest()!=PUBLIC_REFERENCE_SHA256:raise CommandError('Reviewed public reference file changed.')
        refs=json.loads(refbody)['references']
        if review['reviewed_route_references']!=refs:raise CommandError('Report references differ from the checked source review.')
        ep=local_path(root,review['evidence_path']);data=json.loads(ep.read_text(encoding='utf-8'));pages={p['source_url']:p for p in data['pages']}
        for p in data['pages']:
            if Path(p['file']).name!=p['file'] or hashlib.sha256((ep.parent/p['file']).read_bytes()).hexdigest()!=p['sha256']:raise CommandError('Raw source hash mismatch.')
        manifest=json.loads((root/'data/universities/uk/aub/remaining_20_review.json').read_text(encoding='utf-8'));frozen={r['course_id']:r['before']['course'] for r in review['reviews']};old_tuitions=[r['before']['tuition'] for r in review['reviews']]
        fresh,summary=build_review(data,manifest,frozen,old_tuitions,refs)
        if canonical(fresh)!=canonical(review['reviews']):raise CommandError('Review proposals differ from their rebuilt source evidence.')
        rows=[r for r in fresh if r['status'] in {'online_programme_fee_proposal','campus_fee_replacement_proposal','foundation_provisional_fee_proposal'}]
        if len(rows)!=13:raise CommandError('Expected thirteen supported route proposals.')
        report={'university_id':'UK005','generated_at':now().isoformat(),'review_path':str(path),'database_changes':False,'courses':[],'unchanged_pending_ids':[r['course_id'] for r in fresh if r['status']=='route_intake_pending']}
        with transaction.atomic():
            plans=[]
            for row in rows:
                cid=row['course_id'];cs=list(Course.objects.select_for_update().filter(university_id='UK005',course_id=cid));ts=list(Tuition.objects.select_for_update().filter(course_id=cid,residency='INTL'))
                if len(cs)!=1 or len(ts)!=1:raise CommandError(cid+': expected one course and international tuition.')
                c=cs[0];t=ts[0];source=TuitionSource.objects.select_for_update().filter(tuition=t).first();owned=source and isinstance(source.raw_fees,dict) and source.raw_fees.get('importer')==OWNER
                if source and not owned:raise CommandError(cid+': existing source belongs to another correction; no overwrite.')
                after_course,amount,defaults=plan_route(row,pages)
                if t.currency!='GBP' or not c.is_active:raise CommandError(cid+': currency/status changed.')
                if not owned:
                    for k,v in course_state(c).items():
                        if v!=row['before']['course'][k]:raise CommandError(cid+': course changed since preview: '+k)
                    old=row['before']['tuition']
                    if Decimal(str(t.tuition_fee))!=Decimal(str(old['tuition_fee'])) or Decimal(str(t.scholarship or 0))!=Decimal(str(old['scholarship'])) or t.scholarship_source_id!=old['scholarship_source_id']:raise CommandError(cid+': tuition/discount/link changed since preview.')
                elif course_state(c)!=after_course or Decimal(str(t.tuition_fee))!=Decimal(amount) or t.scholarship_source_id is not None or Decimal(str(t.scholarship or 0))!=0:
                    raise CommandError(cid+': previously applied business values changed; manual review needed.')
                # Preserve separately reconciled AUB online/foundation review.
                if owned and isinstance(source.raw_fees.get('online_foundation_awards'),dict) and source.raw_fees['online_foundation_awards'].get('importer')=='aub_online_progression_v1':
                    defaults['raw_fees']['online_foundation_awards']=deepcopy(source.raw_fees['online_foundation_awards'])
                    defaults['raw_fees']['scholarship_review']=deepcopy(source.raw_fees['scholarship_review'])
                before={'course':course_state(c),'tuition':tuition_state(t),'source':source_state(source)};after_t=tuition_state(t);after_t.update(tuition_fee=amount,scholarship='0.00',scholarship_source_id=None)
                after=canonical({'course':after_course,'tuition':after_t,'source':defaults});changed=canonical(before)!=after
                plans.append((c,t,after_course,amount,defaults,before,after,changed));report['courses'].append({'course_id':cid,'changed':changed,'before':before,'after':after})
            names=list(Scholarship.objects.select_for_update().filter(university_id='UK005',name=AWARD_NAME))
            if len(names)>1:raise CommandError('Duplicate managed Fashion award.')
            award=names[0] if names else None;rule=ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None
            desired=rules_for('PG',[CAMPUS_ID],pages[AWARDS]['sha256'])
            if award and (not rule or not isinstance(rule.rules,dict) or rule.rules.get('importer')!=desired['importer']):raise CommandError('Existing Fashion award is not owned by this workflow.')
            award_changed=rule is None or rule.rules!=desired or not rule.verified or rule.source_url!=AWARDS
            note='For these four reviewed two-year online programmes, GBP 500 is a non-refundable acceptance payment allocated to the first tuition instalment after study starts. Included in total tuition; not a campus pre-CAS deposit.'
            prules={'importer':OWNER,'kind':'online_acceptance_payment','academic_year':'2027/28','payment_stage':'acceptance','amount':'500.00','currency':'GBP','course_ids':sorted(ONLINE_IDS),'included_in_tuition':True,'is_cas_deposit':False,'note':note,'source_capture_sha256':pages[ONLINE_FEES]['sha256']}
            policies=list(DepositPolicy.objects.select_for_update().filter(university_id='UK005',academic_year='2027/28'))
            if len(policies)>1:raise CommandError('Multiple AUB year policies require review.')
            policy=policies[0] if policies else None
            if policy and (not isinstance(policy.rules,dict) or policy.rules.get('importer')!=OWNER):raise CommandError('Existing AUB deposit policy belongs to another review.')
            pd={'currency':'GBP','rules':prules,'source_url':ONLINE_FEES,'source_text':note,'source_sha256':pages[ONLINE_FEES]['sha256'],'verified':True}
            policy_changed=policy is None or any(getattr(policy,k)!=v for k,v in pd.items())
            report.update(changed_courses=sum(x[-1] for x in plans),changed_items=sum(x[-1] for x in plans)+int(award_changed)+int(policy_changed),award={'name':AWARD_NAME,'changed':award_changed,'rules':desired},online_acceptance_policy={'changed':policy_changed,**pd})
            report['database_changes']=bool(apply and report['changed_items']);output=root/'logs/aub'/('remaining13-import-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json');output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
            if apply:
                if award_changed:
                    if award is None:award=Scholarship.objects.create(university_id='UK005',name=AWARD_NAME,level='PG',currency='GBP',award_type='Competitive actual award',award_value='3000.00',deadline=None,url=AWARDS,eligibility='Reviewed Fashion campus replacement and confirmed actual award for 2027/28.',requirements='Saved scholarship-page eligibility and AUB-granted amount required.',description='Up to GBP 3,000; never automatically deduct the maximum. Exclusive AUB award.')
                    ScholarshipRule.objects.update_or_create(scholarship=award,defaults={'verified':True,'source_url':AWARDS,'rules':desired,'reviewed_at':now()})
                if policy_changed:DepositPolicy.objects.update_or_create(university_id='UK005',academic_year='2027/28',defaults={**pd,'checked_at':now()})
                for c,t,after_c,amount,defaults,before,after,changed in plans:
                    if not changed:continue
                    fields=[]
                    for k,v in after_c.items():
                        if getattr(c,k)!=v:setattr(c,k,v);fields.append(k)
                    if fields:c.save(update_fields=[*fields,'updated_at'])
                    t.tuition_fee=Decimal(amount);t.scholarship=0;t.scholarship_source_id=None;t.save(update_fields=['tuition_fee','scholarship','scholarship_source'])
                    TuitionSource.objects.update_or_create(tuition=t,defaults={**defaults,'checked_at':now()})
                    TuitionUpdateAudit.objects.create(tuition=t,course_identifier=c.course_id,residency='INTL',before=canonical(before),after=after)
        self.stdout.write('Changed/proposed course records: '+str(report['changed_courses']));self.stdout.write('Changed/proposed total items: '+str(report['changed_items']))
        self.stdout.write('5 reviewed fee schedules; 8 provisional foundation route corrections; 7 unresolved routes untouched.')
        self.stdout.write('Fashion actual-amount rule only; online/foundation scholarship applicability remains pending.')
        self.stdout.write('Online GBP 500 acceptance policy saved on apply: included in tuition, not campus pre-CAS. Numeric stored deposits and EU fees unchanged.')
        self.stdout.write('Report: '+str(output));self.stdout.write('Applied reviewed routes, fees and rules.' if apply else 'Preview only. No database records changed.')

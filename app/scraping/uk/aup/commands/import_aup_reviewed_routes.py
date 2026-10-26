"""Atomic reviewed route/fee import with conditional Alumni and deposit policies."""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import Course,Tuition,TuitionSource,TuitionUpdateAudit,Scholarship,ScholarshipRule,DepositPolicy
from app.scraping.uk.aup.review import plan,INTL,UG,PG,APPLY
from app.scraping.uk.aup.calculation import OWNER,AWARD_OWNER,IDS,PG_IDS,YEAR,SOURCE,ALUMNI_SOURCE,make_rule,policy_rules
NAME='AUP Alumni Progression Award — reviewed MA routes (2027/28)'
BLOCK_OWNER='aup_legacy_source_quarantine_v1'


def canon(obj):return json.loads(json.dumps(obj,cls=DjangoJSONEncoder,sort_keys=True))
def money(x):return str(Decimal(str(x or 0)).quantize(Decimal('.01')))
def source_state(s):
    if s is None:return None
    return canon({k:getattr(s,k) for k in ['academic_year','source_url','fee_basis','raw_fees']})
def tuition_state(t):return dict(tuition_fee=money(t.tuition_fee),scholarship=money(t.scholarship),scholarship_source_id=t.scholarship_source_id,tuition_deposit=money(t.tuition_deposit),currency=t.currency)


class Command(BaseCommand):
    help='Preview/apply 23 AUP reviewed annual fees, conditional Alumni rule and GBP 2,000 pre-CAS policy.'
    def add_arguments(self,p):
        p.add_argument('--preview',help='Path to the successful reviewed-routes preview; newest used by default.')
        p.add_argument('--apply',action='store_true')
        p.add_argument('--accept-course-corrections',action='store_true')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);apply=options['apply']
        if apply and not options['accept_course_corrections']:raise CommandError('Use --accept-course-corrections for the reviewed intake, URLs, codes and title punctuation.')
        if apply and not any('# AUP reviewed23 comparison v1' in p.read_text(encoding='utf-8-sig') for p in (root/'app').rglob('*.py')):raise CommandError('Run connect_comparison.py --apply first.')
        if options.get('preview'):
            pp=Path(options['preview']);pp=pp if pp.is_absolute() else root/pp
        else:
            files=sorted((root/'logs/aup').glob('reviewed-routes-preview-*/preview.json'))
            if not files:raise CommandError('Run preview_aup_reviewed_routes first.')
            pp=files[-1]
        supplied=json.loads(pp.read_text(encoding='utf-8'))
        if supplied.get('university_id')!='UK006' or supplied.get('blocked'):raise CommandError('Expected successful UK006 preview.')
        ep=Path(supplied['evidence_path']);ep=ep if ep.is_absolute() else root/ep
        data=json.loads(ep.read_text(encoding='utf-8'));pages={p['source_url']:p for p in data['pages']}
        manifest=json.loads((root/'data/universities/uk/aup/reviewed_23_routes.json').read_text(encoding='utf-8'))
        for url in {INTL,UG,PG}|{r['source_url'] for r in manifest}:
            p=pages[url]
            if Path(p['file']).name!=p['file'] or hashlib.sha256((ep.parent/p['file']).read_bytes()).hexdigest()!=p['sha256']:raise CommandError('Source hash mismatch: '+url)
        app=supplied['application_capture']
        if app.get('source_url')!=APPLY or Path(app['file']).name!=app['file'] or hashlib.sha256((pp.parent/app['file']).read_bytes()).hexdigest()!=app['sha256']:raise CommandError('Application capture mismatch.')
        rebuilt=plan(data,manifest,app['text'])
        if canon(rebuilt['proposals'])!=canon(supplied['proposals']) or rebuilt['blocked'] or len(rebuilt['proposals'])!=23:raise CommandError('Preview does not match reviewed plan.')
        itext=' '.join(pages[INTL]['text'].split());ptext=' '.join(pages[PG]['text'].split())
        if not all(x in itext for x in ['non-refundable deposit of £2,000','prior to the issuing of a CAS','paid in full upon enrolment','International Fees for 2027/28']):raise CommandError('Deposit amount, stage or enrolment terms require review.')
        if not all(x in ptext for x in ['Alumni Progression Award','15% discount on MA fees','Undergraduate, Pre-Degree and FAD graduates','Postgraduate Fees 2027/28']):raise CommandError('Alumni percentage or qualification scope changed.')
        report=dict(university_id='UK006',generated_at=now().isoformat(),database_changes=False,preview_path=str(pp),course_reviews=[],scholarship_reviews=[],policy_review={})
        folder=root/'logs/aup'/('reviewed23-import-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
        with transaction.atomic():
            plans=[]
            for row in rebuilt['proposals']:
                cid=row['course_id'];cs=list(Course.objects.select_for_update().filter(university_id='UK006',course_id=cid));ts=list(Tuition.objects.select_for_update().filter(course_id=cid,residency='INTL'))
                if len(cs)!=1 or len(ts)!=1:raise CommandError(cid+': expected one course/INTL fee.')
                c,t=cs[0],ts[0];source=TuitionSource.objects.select_for_update().filter(tuition=t).first()
                owned=bool(source and isinstance(source.raw_fees,dict) and source.raw_fees.get('importer')==OWNER)
                if source and not owned:raise CommandError(cid+': unexpected existing source; preserve for review.')
                expected=row['proposed_course'] if owned else row['before_course']
                keys=set(row['proposed_course'])|{'level','location','is_active'}
                for k in keys:
                    want=expected.get(k,row['before_course'].get(k))
                    if getattr(c,k)!=want:raise CommandError(cid+': stored '+k+' differs from reviewed before/after state.')
                if t.currency!='GBP' or money(t.tuition_fee)!=money(row['tuition_fee'] if owned else row['before_international_tuition']['tuition_fee']):raise CommandError(cid+': stored tuition changed since preview.')
                if not owned and (money(t.scholarship)!=money(row['before_international_tuition']['scholarship']) or t.scholarship_source_id!=row['before_international_tuition'].get('scholarship_source_id')):raise CommandError(cid+': stored scholarship changed since preview.')
                if owned and (money(t.scholarship)!='0.00' or t.scholarship_source_id is not None):raise CommandError(cid+': managed numeric discount changed; do not overwrite.')
                amount=row['tuition_fee'];after_c=row['proposed_course']
                raw=dict(importer=OWNER,verified=True,academic_year=YEAR,annual_tuition=amount,currency='GBP',source_fee_category='International',
                    study_mode='campus',default_intake='2027-09',source_course_url=row['course_source_url'],duration=after_c['duration'],location=c.location,
                    source_course_code=after_c.get('ucas_cao_code',''),source_course_name=after_c['title'],
                    qualification=row['qualification'],subject_to_validation=row['subject_to_validation'],duration_verified=True,
                    course_source_sha256=row['course_source_sha256'],fee_source_sha256=row['fee_source_sha256'],international_source_sha256=row['international_fee_source_sha256'],
                    application_source_sha256=app['sha256'],intake_evidence=row['intake_evidence'],
                    fee_note='Published 2027/28 full-time annual international tuition for the exact standard '+row['qualification']+' route, September 2027. Additional course materials or English-language support are excluded; no foundation or placement price is substituted.',
                    scholarship_review={'status':'conditional_alumni' if cid in PG_IDS else 'competitive_cohort_pending','reason':'15% Alumni deduction requires AUP confirmation for the exact intake/period; competitive cohort deadline remains unconfirmed.' if cid in PG_IDS else 'No verified 2027 competitive award scope. Legacy UCA source excluded; no automatic deduction.','questions_location':'scholarship_page'},
                    deposit_review={'status':'verified_conditional','source_url':SOURCE,'amount':'2000.00','message':'Student visa required: GBP 2,000 before CAS, included in tuition; remaining balance due in full at enrolment.'})
                defaults=dict(academic_year=YEAR,source_url=row['fee_source_url'],fee_basis='annual_tuition',raw_fees=raw)
                before=dict(course={k:getattr(c,k) for k in keys},tuition=tuition_state(t),source=source_state(source))
                after=dict(course={**before['course'],**after_c},tuition={**before['tuition'],'tuition_fee':amount,'scholarship':'0.00','scholarship_source_id':None},source=canon(defaults))
                changed=canon(before)!=canon(after);plans.append((c,t,after_c,amount,defaults,before,after,changed));report['course_reviews'].append(dict(course_id=cid,changed=changed,before=canon(before),after=canon(after)))
            matches=list(Scholarship.objects.select_for_update().filter(university_id='UK006',name=NAME))
            if len(matches)>1:raise CommandError('Duplicate managed Alumni award.')
            award=matches[0] if matches else None;rule=ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None
            if award and (not rule or not isinstance(rule.rules,dict) or rule.rules.get('importer')!=AWARD_OWNER):raise CommandError('Managed award ownership conflict.')
            desired=make_rule();rule_changed=not rule or not rule.verified or rule.source_url!=ALUMNI_SOURCE or rule.rules!=desired
            report['scholarship_reviews'].append(dict(name=NAME,changed=bool(rule_changed),before=canon(rule.rules) if rule else None,after=desired))
            blocks=[]
            for issue in data.get('existing_award_source_issues',[]):
                legacy=Scholarship.objects.select_for_update().get(pk=issue['scholarship_id'],university_id='UK006')
                if legacy.url!=issue['source_url']:raise CommandError('Legacy award URL changed; review rather than quarantine blindly.')
                lr=ScholarshipRule.objects.select_for_update().filter(scholarship=legacy).first()
                if lr and (not isinstance(lr.rules,dict) or lr.rules.get('importer')!=BLOCK_OWNER):raise CommandError('Legacy award now has another managed rule; review it first.')
                blocked=dict(importer=BLOCK_OWNER,status='blocked_external_university_source',reason=issue['reason'],original_source_url=legacy.url,questions_location='scholarship_page')
                changed=lr is None or lr.verified or lr.rules!=blocked or lr.source_url!=legacy.url
                blocks.append((legacy,blocked,changed));report['scholarship_reviews'].append(dict(scholarship_id=legacy.pk,changed=bool(changed),verified=False,before=canon(lr.rules) if lr else None,after=blocked))
            policies=list(DepositPolicy.objects.select_for_update().filter(university_id='UK006',academic_year=YEAR))
            if len(policies)>1:raise CommandError('Multiple AUP year policies require review.')
            policy=policies[0] if policies else None
            if policy and (not isinstance(policy.rules,dict) or policy.rules.get('importer')!=OWNER):raise CommandError('AUP policy ownership conflict.')
            pd=dict(currency='GBP',rules=policy_rules(),source_url=SOURCE,source_text='Non-refundable GBP 2,000 deposit before CAS for applicants needing a Student visa. Included in tuition; balance in full at enrolment.',source_sha256=pages[INTL]['sha256'],verified=True)
            policy_changed=policy is None or any(getattr(policy,k)!=v for k,v in pd.items())
            report['policy_review']=dict(changed=policy_changed,before=canon(policy.rules) if policy else None,after=canon(pd))
            report.update(changed_courses=sum(x[-1] for x in plans),changed_items=sum(x[-1] for x in plans)+int(bool(rule_changed))+sum(int(x[-1]) for x in blocks)+int(policy_changed))
            if apply:
                if rule_changed:
                    if award is None:award=Scholarship.objects.create(university_id='UK006',name=NAME,level='PG',currency='GBP',award_type='Conditional Alumni tuition discount',award_value='15.00',deadline=None,url=ALUMNI_SOURCE,eligibility='AUP qualifying undergraduate, pre-degree or Foundation Diploma graduate; reviewed full-time MA.',requirements='AUP confirmation for exact September 2027 intake/period, international fee status, offer, no other award combined.',description='15% of original annual tuition, conditional on saved verified applicant facts. Not an automatic award to all applicants.')
                    ScholarshipRule.objects.update_or_create(scholarship=award,defaults=dict(verified=True,source_url=ALUMNI_SOURCE,rules=desired,reviewed_at=now()))
                for legacy,blocked,changed in blocks:
                    if changed:ScholarshipRule.objects.update_or_create(scholarship=legacy,defaults=dict(verified=False,source_url=legacy.url,rules=blocked,reviewed_at=now()))
                if policy_changed:DepositPolicy.objects.update_or_create(university_id='UK006',academic_year=YEAR,defaults={**pd,'checked_at':now()})
                for c,t,after_c,amount,defaults,before,after,changed in plans:
                    if not changed:continue
                    fields=[]
                    for k,v in after_c.items():
                        if getattr(c,k)!=v:setattr(c,k,v);fields.append(k)
                    if fields:c.save(update_fields=[*fields,'updated_at'])
                    t.tuition_fee=Decimal(amount);t.scholarship=0;t.scholarship_source_id=None;t.save(update_fields=['tuition_fee','scholarship','scholarship_source'])
                    TuitionSource.objects.update_or_create(tuition=t,defaults={**defaults,'checked_at':now()})
                    TuitionUpdateAudit.objects.create(tuition=t,course_identifier=c.course_id,residency='INTL',before=canon(before),after=canon(after))
            report['database_changes']=bool(apply and report['changed_items'])
            (folder/'reconciliation.json').write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        self.stdout.write('Reviewed course changes: '+str(report['changed_courses']));self.stdout.write('Changed/proposed items: '+str(report['changed_items']))
        self.stdout.write('Seven MA routes: conditional Alumni 15%; GBP 18,000 - GBP 2,700 = GBP 15,300 if confirmed for the applicant and intake.')
        self.stdout.write('Deposit scope: 23; GBP 2,000 before CAS only where a Student visa is required, included in tuition.')
        self.stdout.write('Competitive 2027 awards remain pending. UCA-linked legacy award blocked; unverified numeric discounts cleared on apply with audit.')
        self.stdout.write('Three unresolved routes unchanged. Numeric deposits and EU tuition unchanged.')
        self.stdout.write('Report: '+str(folder/'reconciliation.json'));self.stdout.write('Applied reviewed fees, conditional rule and policy.' if apply else 'Preview only. No database records changed.')

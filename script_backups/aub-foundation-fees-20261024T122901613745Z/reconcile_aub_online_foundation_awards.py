"""Save source-confirmed online progression rule; retain integrated-Year-0 review."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import Course,Tuition,TuitionSource,TuitionUpdateAudit,Scholarship,ScholarshipRule
from app.scraping.uk.aub.calculation import FACTS
from app.scraping.uk.aub.remaining_calculation import OWNER,AWARD_OWNER,PROGRESSION_FACT,ONLINE_IDS,FOUNDATION_IDS
from app.scraping.uk.aub.commands.import_aub_first_30 import canonical,source_state

SOURCE='https://aub.ac.uk/international/international-scholarships/international-progression-bursary'
OVERVIEW='https://aub.ac.uk/international/international-scholarships'
COMPETITIVE=OVERVIEW+'/international-undergraduate-and-postgraduate-scholarships'
NAME='AUB International Online MA Progression Bursary (2027/28)'
DEFAULT='logs/aub/source-review-20261024T004000918202Z/source-review.json'


def make_rule(sha):
    questions=[{'key':k,'type':'boolean','label':v} for k,v in FACTS.items()]
    for q in questions:
        if q['key']=='aub_award_granted':q['label']='Has AUB confirmed that you have been granted its online postgraduate progression bursary?'
        if q['key']=='aub_award_accepted':q['label']='Have you accepted this progression bursary under AUB’s instructions?'
    questions.append({'key':PROGRESSION_FACT,'type':'boolean','label':'Are you a current AUB undergraduate, or did you complete an AUB undergraduate degree within the two years before progressing to this online MA? Use AUB’s assessment if unsure.'})
    conditions={k:[True] for k in FACTS};conditions.update({PROGRESSION_FACT:[True],'course_id':sorted(ONLINE_IDS),'fee_status':['INTL']})
    return dict(importer=AWARD_OWNER,benefit='tuition',kind='fixed',value='1995.00',currency='GBP',academic_year='2027/28',study_years=[1],conditions=conditions,award_status='conditional',stacking='exclusive',compatible_ids=[],percentage_basis='original_tuition',fee_basis_confirmed=True,fee_basis='total_programme_fee',scope_note='Four reviewed September 2027 two-year online MAs only. GBP 1,995 once against the GBP 9,975 programme total; not an annual deduction. AUB grant and acceptance required. No other AUB award stacked.',source_capture_sha256=sha,eligibility_questions=questions,questions_location='scholarship_page')


def validate_sources(pages):
    s=' '.join(pages[SOURCE]['text'].split())
    required=['£1,995 for online MA courses','£7,980 (online)','January 2027, September 2027, or January 2028','Be classified as an overseas fee payer','within the past two years']
    # The current wording uses "in the past two years" for the UG-to-PG criterion.
    required[-1]='in the past two years'
    if not all(x in s for x in required):raise ValueError('Online international progression amount, cohort or eligibility wording changed; review source before applying.')
    overview=pages[OVERVIEW]['text']
    if 'all international scholarships are for on-campus courses only and do not apply to online courses' not in overview:raise ValueError('Campus competitive-award exclusion changed; review separately.')
    if 'Foundation Diploma' not in s or 'first year only' not in pages[COMPETITIVE]['text']:raise ValueError('Foundation distinction or competitive period needs review.')


class Command(BaseCommand):
    help='Reconcile online international progression bursary and retain integrated-foundation Year 0 applicability review.'
    def add_arguments(self,parser):
        parser.add_argument('--evidence',default=DEFAULT)
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);apply=options['apply']
        if apply and not any('# AUB online progression labels v1' in p.read_text(encoding='utf-8-sig') for p in (root/'app').rglob('*.py')):raise CommandError('Run connect_online_awards.py --apply before applying the rule.')
        ep=Path(options['evidence']);ep=ep if ep.is_absolute() else root/ep;data=json.loads(ep.read_text(encoding='utf-8'));pages={p['source_url']:p for p in data['pages']}
        if data.get('university_id')!='UK005':raise CommandError('Expected AUB source evidence.')
        for url in [SOURCE,OVERVIEW,COMPETITIVE]:
            p=pages[url]
            if Path(p['file']).name!=p['file'] or hashlib.sha256((ep.parent/p['file']).read_bytes()).hexdigest()!=p['sha256']:raise CommandError('Source hash mismatch: '+url)
        try:validate_sources(pages)
        except ValueError as exc:raise CommandError(str(exc)) from exc
        desired=make_rule(pages[SOURCE]['sha256']);report={'university_id':'UK005','generated_at':now().isoformat(),'database_changes':False,'course_reviews':[],'source_url':SOURCE}
        with transaction.atomic():
            awards=list(Scholarship.objects.select_for_update().filter(university_id='UK005',name=NAME))
            if len(awards)>1:raise CommandError('Duplicate managed online progression award.')
            award=awards[0] if awards else None;rule=ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None
            if award and (not rule or not isinstance(rule.rules,dict) or rule.rules.get('importer')!=AWARD_OWNER):raise CommandError('Existing award is outside this reconciliation.')
            changed_rule=rule is None or rule.rules!=desired or not rule.verified or rule.source_url!=SOURCE
            plans=[]
            for cid in sorted(ONLINE_IDS|FOUNDATION_IDS):
                courses=list(Course.objects.select_for_update().filter(university_id='UK005',course_id=cid));tuitions=list(Tuition.objects.select_for_update().filter(course_id=cid,residency='INTL'))
                if len(courses)!=1 or len(tuitions)!=1:raise CommandError(cid+': expected one active reviewed course and international tuition.')
                c=courses[0];t=tuitions[0];source=TuitionSource.objects.select_for_update().filter(tuition=t).first()
                if not c.is_active or c.start_year!=2027 or c.start_month!='September' or not source or not isinstance(source.raw_fees,dict) or source.raw_fees.get('importer')!=OWNER or source.academic_year!='2027/28':raise CommandError(cid+': apply the reviewed route import first.')
                online=cid in ONLINE_IDS
                if online and (source.fee_basis!='total_programme_fee' or source.raw_fees.get('programme_total')!='9975.00' or str(t.currency)!='GBP'):raise CommandError(cid+': online fee basis changed.')
                raw=deepcopy(source.raw_fees)
                reason=('Verified conditional international online progression bursary: GBP 1,995 against the GBP 9,975 programme total, net GBP 7,980 if eligible and awarded. Campus international competitive scholarships do not apply online.' if online else 'Integrated foundation Year 0 award applicability remains unconfirmed. Foundation Diploma bursaries and progression from an already completed Foundation Diploma are separate. The UG competitive first-year wording does not explicitly confirm Year 0. Provisional tuition remains unverified as final; no deduction.')
                raw['online_foundation_awards']={'importer':AWARD_OWNER,'status':'verified_conditional_online_progression' if online else 'year_zero_applicability_unconfirmed','source_url':SOURCE,'source_sha256':pages[SOURCE]['sha256'],'competitive_source_url':COMPETITIVE,'campus_exclusion_source_url':OVERVIEW,'reason':reason,'questions_location':'scholarship_page'}
                raw['scholarship_review']={'status':raw['online_foundation_awards']['status'],'reason':reason,'questions_location':'scholarship_page','eligibility_questions':desired['eligibility_questions'] if online else []}
                before=source_state(source);changed=source.raw_fees!=raw;after={**before,'raw_fees':raw};plans.append((t,source,raw,before,after,changed));report['course_reviews'].append({'course_id':cid,'changed':changed,'review':raw['online_foundation_awards']})
            report.update(changed_items=int(changed_rule)+sum(p[-1] for p in plans),rule={'changed':changed_rule,'name':NAME,'rules':desired});report['database_changes']=bool(apply and report['changed_items'])
            output=root/'logs/aub'/('online-foundation-awards-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json');output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
            if apply:
                if changed_rule:
                    if award is None:award=Scholarship.objects.create(university_id='UK005',name=NAME,level='PG',currency='GBP',award_type='Conditional progression bursary',award_value='1995.00',deadline=None,url=SOURCE,eligibility='AUB undergraduate progression within the published period; overseas fee status and reviewed online MA intake.',requirements='Timely application, AUB grant and acceptance, no other AUB award; saved personal facts required.',description='GBP 1,995 once against the two-year online programme total; not automatic and not annual.')
                    ScholarshipRule.objects.update_or_create(scholarship=award,defaults={'verified':True,'source_url':SOURCE,'rules':desired,'reviewed_at':now()})
                for t,source,raw,before,after,changed in plans:
                    if changed:
                        source.raw_fees=raw;source.checked_at=now();source.save(update_fields=['raw_fees','checked_at'])
                        TuitionUpdateAudit.objects.create(tuition=t,course_identifier=t.course_id,residency='INTL',before={'source':before},after={'source':canonical(after)})
        self.stdout.write('Online MA scope: 4; GBP 9,975 - GBP 1,995 = GBP 7,980 if eligible and awarded.')
        self.stdout.write('Integrated foundation Year 0: 8 courses remain applicability-unconfirmed; no deduction.')
        self.stdout.write('Changed/proposed items: '+str(report['changed_items']));self.stdout.write('Report: '+str(output))
        self.stdout.write('Saved rule and course review metadata.' if apply else 'Preview only. No database records changed.')
        self.stdout.write('Gross tuition, stored numeric discounts/deposits and payment policies unchanged. Scholarship questions remain for the scholarship page.')

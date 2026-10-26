"""Read-only live AUB audit; update the existing combined register and next export."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from app.models import University, Course, Tuition, TuitionSource, Scholarship, ScholarshipRule, DepositPolicy
from app.scraping.uk.aub import calculation as campus
from app.scraping.uk.aub.remaining_calculation import fee_error, deposit, ONLINE_IDS, FOUNDATION_IDS, AWARD_OWNER as ONLINE_OWNER
from app.scraping.uk.aub.early_payment import OWNER as WAIVER_OWNER, make_rule, SCOPES
from app.scraping.uk.aub.handoff import OWNER, summarize, merge_register

REVIEWED=['UK001','UK002','UK003','UK004','UK005']


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
    os.replace(tmp,path)


def export_school(uid):
    return dict(database_changes=False, university_id=uid,
        university=University.objects.filter(university_id=uid).values().get(),
        courses=list(Course.objects.filter(university_id=uid).order_by('course_id').values()),
        tuitions=list(Tuition.objects.filter(course__university_id=uid).values()),
        sources=list(TuitionSource.objects.filter(tuition__course__university_id=uid).values()),
        scholarships=list(Scholarship.objects.filter(university_id=uid).values()),
        scholarship_rules=list(ScholarshipRule.objects.filter(scholarship__university_id=uid).values()),
        deposit_policies=list(DepositPolicy.objects.filter(university_id=uid).values()))


def recognized(rule,award):
    r=rule.rules
    if not rule.verified or not rule.source_url or not isinstance(r,dict) or r.get('academic_year')!='2027/28' or award.currency!='GBP':return False
    conditions=r.get('conditions')
    if not isinstance(conditions,dict) or not isinstance(conditions.get('course_id'),list):return False
    if r.get('importer')==WAIVER_OWNER:
        return award.level in SCOPES and r==make_rule(award.level)
    common=(r.get('benefit')=='tuition' and r.get('currency')=='GBP' and r.get('stacking')=='exclusive'
        and r.get('fee_basis_confirmed') is True and r.get('study_years')==[1]
        and all(conditions.get(k)==[True] for k in campus.FACTS)
        and conditions.get('fee_status')==['INTL'])
    if r.get('importer')==campus.AWARD_OWNER:
        maximum='5000.00' if award.level=='UG' else '3000.00'
        return common and r.get('kind')=='confirmed_amount' and r.get('published_maximum')==maximum and conditions.get('aub_award_amount_valid')==[True]
    if r.get('importer')==ONLINE_OWNER:
        return common and r.get('kind')=='fixed' and r.get('value')=='1995.00' and conditions.get('aub_progression_qualifying_undergraduate')==[True] and set(conditions['course_id'])==ONLINE_IDS
    return False


class Command(BaseCommand):
    help='Audit final AUB coverage, preserve the four previous university entries, and export the next UK university. No database writes.'

    def add_arguments(self,parser):
        parser.add_argument('--register',help='Optional path to the existing combined Markdown register.')

    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        reference_path=root/'data/universities/uk/aub/final_review_reference.json'
        reference=json.loads(reference_path.read_text(encoding='utf-8'))
        reasons={r['course_id']:r for r in reference['pending_routes']}
        generated=datetime.now(timezone.utc).isoformat()
        folder=root/'logs/scraping-progress'/('aub-handoff-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        base=root/'logs/scraping-progress'
        md=Path(options['register']) if options.get('register') else base/'university_scraping_progress_register.md'
        if not md.is_absolute():md=root/md
        baseline_path=md if md.is_file() else root/'data/universities/progress/aub_prior_register_reference.md'
        baseline=baseline_path.read_text(encoding='utf-8-sig')
        jp=base/'university_scraping_progress_register.json'
        previous=json.loads(jp.read_text(encoding='utf-8')) if jp.exists() else {}
        if previous and (previous.get('owner')!='university_scraping_progress_v1' or not isinstance(previous.get('universities'),dict)):
            raise CommandError('Existing combined JSON has an unfamiliar structure; no register overwrite.')
        anomalies=[];rows=[];rule_rows=[]
        with transaction.atomic():
            courses=list(Course.objects.filter(university_id='UK005').order_by('course_id'))
            if not courses:raise CommandError('AUB UK005 courses not found.')
            tuition_groups=defaultdict(list)
            for t in Tuition.objects.filter(course__university_id='UK005',residency='INTL').select_related('course'):
                tuition_groups[t.course_id].append(t)
            sources={s.tuition_id:s for s in TuitionSource.objects.filter(tuition__course__university_id='UK005',tuition__residency='INTL')}
            awards={s.pk:s for s in Scholarship.objects.filter(university_id='UK005')}
            rule_map=defaultdict(list)
            for rule in ScholarshipRule.objects.filter(scholarship__university_id='UK005'):
                award=awards[rule.scholarship_id];usable=recognized(rule,award)
                rule_rows.append(dict(scholarship_id=str(award.pk),name=award.name,level=award.level,
                    verified=bool(rule.verified),adapter_supported_scope=bool(usable),source_url=rule.source_url,rules=rule.rules))
                if usable:
                    for cid in rule.rules['conditions']['course_id']:rule_map[cid].append((award,rule))
            policies=list(DepositPolicy.objects.filter(university_id='UK005').values())
            for c in courses:
                if not c.is_active:continue
                group=tuition_groups[c.course_id];t=group[0] if len(group)==1 else None
                source=sources.get(t.pk) if t else None;raw=source.raw_fees if source and isinstance(source.raw_fees,dict) else {}
                basis='programme_total' if c.course_id in ONLINE_IDS else 'annual'
                error='Expected one international tuition record; found '+str(len(group)) if t is None else fee_error(t,source,basis)
                if c.start_year!=2027 or c.start_month!='September':error=error or 'Course intake does not match reviewed September 2027 cohort.'
                if error is None and (t.residency!='INTL' or t.currency!='GBP'):error='Currency or residency mismatch.'
                if c.course_id not in reasons and error:anomalies.append(c.course_id+': '+error)
                scoped=[]
                if error is None:
                    for award,rule in rule_map[c.course_id]:
                        r=rule.rules
                        if award.level!=c.level:continue
                        if c.course_id in ONLINE_IDS and r.get('importer')!=ONLINE_OWNER:continue
                        if c.course_id not in ONLINE_IDS and r.get('importer')==ONLINE_OWNER:continue
                        if c.course_id in FOUNDATION_IDS and r.get('importer')!=WAIVER_OWNER:continue
                        scoped.append((award,rule))
                payment=deposit(tuition=t,academic_year=source.academic_year) if error is None else dict(available=False,amount=None,note='Route/intake and applicable payment policy require confirmation.')
                reason=reasons.get(c.course_id,{})
                rows.append(dict(course_id=c.course_id,title=c.title,level=c.level,location=c.location,
                    duration=c.duration,start_month=c.start_month,start_year=c.start_year,course_url=c.course_url,
                    fee_verified=error is None,fee_basis=source.fee_basis if source else None,
                    academic_year=source.academic_year if source else None,currency=t.currency if t else None,
                    published_tuition=str(t.tuition_fee) if t and error is None else None,
                    fee_reason=reason.get('reason',error) if error else None,
                    next_action=reason.get('next_action','Review unexpected audit discrepancy before handoff.'),
                    source_url=source.source_url if source else None,
                    conditional_award_rule_ids=[str(a.pk) for a,r in scoped],
                    award_kinds=sorted({r.rules['kind'] for a,r in scoped}),
                    early_payment_scope=any(r.rules.get('importer')==WAIVER_OWNER for a,r in scoped),
                    online_progression_scope=any(r.rules.get('importer')==ONLINE_OWNER for a,r in scoped),
                    foundation_year_zero=c.course_id in FOUNDATION_IDS,
                    validation_status=raw.get('subject_to_validation'),duration_verified=raw.get('duration_verified',False),
                    scholarship_review=raw.get('scholarship_review'),payment=payment))
            summary=summarize(rows,len(courses),sum(c.is_active for c in courses),rule_rows,policies)
            inventory=list(University.objects.filter(country='UK').order_by('university_id').values('university_id','name','slug'))
            nxt=University.objects.filter(country='UK').exclude(university_id__in=REVIEWED).order_by('university_id').first()
            snapshot=export_school('UK005')
            next_data=export_school(nxt.university_id) if nxt else None
        expected={'total_course_records':50,'active_courses':50,'verified_international_fees':44,'pending_international_fees':6,
            'verified_fee_courses_with_conditional_award_scope':44,'verified_fee_courses_with_actual_amount_award_scope':32,
            'verified_fee_courses_with_early_payment_waiver_scope':39,'verified_fee_courses_with_fixed_online_progression_scope':4,
            'foundation_year_zero_competitive_scholarship_pending':8,'verified_fee_courses_with_numeric_payment_available':4,
            'verified_fee_courses_without_verified_numeric_payment':40}
        differences={k:dict(expected=v,actual=summary[k]) for k,v in expected.items() if summary[k]!=v}
        if {r['course_id'] for r in rows if not r['fee_verified']} != set(reasons):anomalies.append('Pending course IDs differ from the six reviewed route/intake cases.')
        if summary['verified_fee_bases']!={'annual_tuition':40,'total_programme_fee':4}:anomalies.append('Verified fee-basis totals differ from expected 40 annual / 4 programme totals.')
        report=dict(owner=OWNER,university_id='UK005',university_name='Arts University Bournemouth',generated_at=generated,
            status='reviewed_with_outstanding_cases',database_changes=False,summary=summary,courses=rows,
            pending_fee_cases=[r for r in rows if not r['fee_verified']],scholarship_rules=rule_rows,deposit_policies=policies,
            reference_review=reference,expected_count_differences=differences,anomalies=anomalies,
            next_university_id=nxt.university_id if nxt else None,uk_inventory=inventory,
            audit_path=str(folder/'aub-final-audit.json'))
        # Validate every register merge before writing either canonical register.
        try:merged=merge_register(baseline,report)
        except ValueError as exc:raise CommandError(str(exc)) from exc
        combined=deepcopy(previous) if previous else dict(owner='university_scraping_progress_v1',universities={})
        combined.update(updated_at=generated,database_changes=False,uk_inventory=inventory)
        combined['universities']['UK005']=dict(audit_path=report['audit_path'],audit_timestamp=datetime.fromisoformat(generated).timestamp(),report=report)
        combined['prior_markdown_provenance']=str(baseline_path)
        combined['prior_university_json_entries_not_available']=[uid for uid in REVIEWED[:-1] if uid not in combined['universities']]
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'previous-progress-register.md').write_text(baseline,encoding='utf-8')
        if jp.exists():(folder/'previous-progress-register.json').write_bytes(jp.read_bytes())
        write_json(folder/'aub-final-audit.json',report)
        write_json(folder/'aub-database-snapshot.json',snapshot)
        write_json(folder/'cumulative-progress-register.json',combined)
        (folder/'university_scraping_progress_register.md').write_text(merged,encoding='utf-8')
        if next_data:write_json(folder/'next-uk-university-export.json',next_data)
        # An anomalous audit is saved for investigation, but cannot replace the canonical register.
        if differences or anomalies:
            self.stdout.write(json.dumps(summary,indent=2));self.stdout.write('Audit: '+report['audit_path'])
            raise CommandError('Audit differences saved; canonical register not changed. Review '+str(differences)+' '+str(anomalies))
        md.parent.mkdir(parents=True,exist_ok=True)
        tmp=md.with_name(md.name+'.tmp');tmp.write_text(merged,encoding='utf-8');os.replace(tmp,md)
        write_json(jp,combined)
        write_json(root/'data/universities/progress/aub_latest_handoff.json',report)
        self.stdout.write(json.dumps(summary,indent=2))
        self.stdout.write('AUB audit: '+report['audit_path'])
        self.stdout.write('Existing combined register updated: '+str(md))
        self.stdout.write('Combined JSON updated: '+str(jp))
        if nxt:
            self.stdout.write('Next university: '+nxt.university_id+' '+nxt.name)
            self.stdout.write('Courses: '+str(len(next_data['courses'])))
            self.stdout.write('Next export: '+str(folder/'next-uk-university-export.json'))
        self.stdout.write('Reviewed with outstanding cases. Report files saved; no database records changed.')

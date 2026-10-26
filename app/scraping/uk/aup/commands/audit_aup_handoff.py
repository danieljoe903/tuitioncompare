"""Read-only database audit; append AUP to existing cumulative report files."""
from copy import deepcopy
from pathlib import Path
import json, os
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import University, Course, Tuition, TuitionSource, ScholarshipRule, DepositPolicy, TuitionUpdateAudit
from app.scraping.uk.aup.calculation import IDS, PG_IDS, YEAR, AWARD_OWNER, ALUMNI_SOURCE, make_rule, policy_rules, fee_error
from app.scraping.uk.aup.award_status import competitive_award_notices
from app.scraping.uk.aup.handoff import merge_register
from app.scraping.uk.aub.commands.audit_aub_handoff import export_school

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp,path)

class Command(BaseCommand):
    help='Audit AUP, preserve previous five entries, update combined register and export next UK university. No database writes.'

    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);base=root/'logs/scraping-progress'
        md=base/'university_scraping_progress_register.md';jp=base/'university_scraping_progress_register.json'
        if not md.is_file() or not jp.is_file():
            raise CommandError('Both existing combined registers are required. No replacement register will be invented.')
        original_md=md.read_bytes();original_json=jp.read_bytes()
        text=original_md.decode('utf-8-sig');previous=json.loads(original_json)
        if previous.get('owner')!='university_scraping_progress_v1' or not isinstance(previous.get('universities'),dict):
            raise CommandError('Unfamiliar combined JSON structure; no overwrite.')
        if 'UK005' not in previous['universities']:
            raise CommandError('Existing AUB final JSON entry missing; restore the combined register first.')
        generated=now();folder=base/('aup-handoff-'+generated.strftime('%Y%m%dT%H%M%S%fZ'))
        errors=[];rows=[]
        with transaction.atomic():
            courses=list(Course.objects.filter(university_id='UK006').order_by('course_id'))
            for c in courses:
                group=list(Tuition.objects.filter(course=c,residency='INTL'))
                t=group[0] if len(group)==1 else None
                sources=list(TuitionSource.objects.filter(tuition=t)) if t else []
                source=sources[0] if len(sources)==1 else None
                error=fee_error(t,source) if t else 'Expected one international tuition record.'
                rows.append(dict(course_id=c.course_id,title=c.title,active=c.is_active,
                    fee_verified=c.is_active and error is None,fee_reason=error,
                    academic_year=source.academic_year if source else None,
                    fee_basis=source.fee_basis if source else None,
                    tuition=str(t.tuition_fee) if c.is_active and error is None else None))
            valid={r['course_id'] for r in rows if r['fee_verified']}
            pending={r['course_id'] for r in rows if r['active'] and not r['fee_verified']}
            inactive={r['course_id'] for r in rows if not r['active']}
            if len(courses)!=26 or valid!=IDS or pending!={'C_AUP006_01','C_UK006_18'} or inactive!={'C_UK006_24'}:
                errors.append('Course/verified/pending/inactive inventory differs from the reviewed 26/23/2/1 state.')
            rules=list(ScholarshipRule.objects.filter(scholarship__university_id='UK006'))
            supported=[r for r in rules if r.verified and r.source_url==ALUMNI_SOURCE and r.rules==make_rule()]
            if len(supported)!=1:errors.append('Expected exactly one reviewed conditional Alumni rule.')
            policies=list(DepositPolicy.objects.filter(university_id='UK006',academic_year=YEAR))
            supported_policies=[p for p in policies if p.verified and p.currency=='GBP' and p.rules==policy_rules() and p.source_url=='https://www.aup.ac.uk/international/finance']
            if len(supported_policies)!=1:errors.append('Expected exactly one reviewed deposit policy.')
            audits=TuitionUpdateAudit.objects.filter(course_identifier='C_UK006_24',residency='INTL')
            if not any(isinstance(a.after,dict) and a.after.get('importer')=='aup_painting_transition_v1' and a.after.get('canonical_course_id')=='C_UK006_17' for a in audits):
                errors.append('Painting canonical transition audit missing.')
            summary=dict(total_course_records=len(courses),active_courses=sum(c.is_active for c in courses),
                inactive_courses=len(inactive),verified_international_fees=len(valid),pending_international_fees=len(pending),
                verified_annual_fee_courses=len(valid),verified_fee_courses_with_conditional_alumni_scope=len(valid & PG_IDS) if supported else 0,
                verified_fee_courses_without_supported_deduction_scope=len(valid-(PG_IDS if supported else set())),
                scholarship_rule_records=len(rules),verified_scholarship_rule_records=sum(r.verified for r in rules),
                unverified_scholarship_rule_records=sum(not r.verified for r in rules),
                ended_published_competitive_award_cycles=sum(r['status']=='ended' for r in competitive_award_notices()),
                deposit_policy_records=len(policies),verified_deposit_policy_records=sum(p.verified for p in policies),
                verified_fee_courses_with_conditional_deposit_scope=len(valid) if supported_policies else 0)
            reviewed=['UK001','UK002','UK003','UK004','UK005','UK006']
            nxt=University.objects.filter(country='UK').exclude(university_id__in=reviewed).order_by('university_id').first()
            snapshot=export_school('UK006');next_data=export_school(nxt.university_id) if nxt else None
        report=dict(owner='aup_final_handoff_v1',university_id='UK006',university_name='Arts University Plymouth',
            generated_at=generated.isoformat(),status='reviewed_with_outstanding_cases',database_changes=False,
            summary=summary,courses=rows,competitive_award_notices=competitive_award_notices(),
            follow_up='Recheck Graphic Design Top-up, MA Drawing and September 2027 competitive scholarship availability when official terms are updated.',
            anomalies=errors,next_university_id=nxt.university_id if nxt else None,audit_path=str(folder/'aup-final-audit.json'))
        try:merged=merge_register(text,report)
        except ValueError as exc:raise CommandError(str(exc)) from exc
        combined=deepcopy(previous)
        combined['universities']['UK006']=dict(audit_path=report['audit_path'],audit_timestamp=generated.timestamp(),report=report)
        combined.update(updated_at=generated.isoformat(),database_changes=False)
        folder.mkdir(parents=True)
        (folder/'previous-progress-register.md').write_bytes(original_md)
        (folder/'previous-progress-register.json').write_bytes(original_json)
        write_json(folder/'aup-final-audit.json',report);write_json(folder/'aup-database-snapshot.json',snapshot)
        if next_data:write_json(folder/'next-uk-university-export.json',next_data)
        if errors:raise CommandError('Audit saved; combined register unchanged: '+'; '.join(errors))
        if md.read_bytes()!=original_md or jp.read_bytes()!=original_json:
            raise CommandError('Register changed during audit; rerun rather than overwrite newer work.')
        temp=md.with_name(md.name+'.tmp');temp.write_text(merged,encoding='utf-8');os.replace(temp,md)
        write_json(jp,combined)
        write_json(root/'data/universities/progress/aup_latest_handoff.json',report)
        self.stdout.write(json.dumps(summary,indent=2))
        self.stdout.write('AUP audit: '+report['audit_path'])
        self.stdout.write('Existing combined Markdown and JSON updated; previous five entries preserved.')
        if nxt:self.stdout.write('Next university: '+nxt.university_id+' '+nxt.name+'; courses: '+str(len(next_data['courses'])))
        self.stdout.write('Next export: '+str(folder/'next-uk-university-export.json'))
        self.stdout.write('Outstanding routes and September 2027 scholarship follow-up recorded. No database records changed.')

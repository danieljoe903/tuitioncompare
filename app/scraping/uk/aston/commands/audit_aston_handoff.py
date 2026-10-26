"""Audit saved Aston data and update the existing combined file register only."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json,os
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import University,Course,Tuition,TuitionSource,ScholarshipRule,DepositPolicy
from app.scraping.uk.aston.calculation import all_fee_manifest,fee_error
from app.scraping.uk.aston.awards import specs,make_rule,deposit_rule,FUNDING,notices
from app.scraping.uk.aston.handoff import merge_register,PRIOR
from app.scraping.uk.aub.commands.audit_aub_handoff import export_school

def encoded(value):return json.dumps(value,cls=DjangoJSONEncoder,ensure_ascii=False,indent=2)
def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp');temp.write_text(encoded(value),encoding='utf-8');os.replace(temp,path)

class Command(BaseCommand):
    help='Audit Aston, retain reasons for seven unconfirmed routes and MBA awards, preserve prior six university entries. No database writes.'
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);base=root/'logs/scraping-progress'
        md=base/'university_scraping_progress_register.md';jp=base/'university_scraping_progress_register.json'
        if not md.is_file() or not jp.is_file():raise CommandError('Both existing combined progress registers are required; no replacement register is invented.')
        original_md=md.read_bytes();original_json=jp.read_bytes()
        try:previous=json.loads(original_json);text=original_md.decode('utf-8-sig')
        except (ValueError,UnicodeError) as exc:raise CommandError('Malformed combined register; no overwrite.') from exc
        if previous.get('owner')!='university_scraping_progress_v1' or not isinstance(previous.get('universities'),dict):
            raise CommandError('Unfamiliar combined JSON structure; no overwrite.')
        if not set(PRIOR).issubset(previous['universities']):raise CommandError('Previous six university JSON entries must be present; no overwrite.')
        followups=json.loads((root/'data/universities/uk/aston/review_followups.json').read_text(encoding='utf-8'))
        if followups.get('university_id')!='UK007' or len(followups.get('routes',{}))!=7:raise CommandError('Expected seven Aston follow-up messages.')
        generated=now();folder=base/('aston-handoff-'+generated.strftime('%Y%m%dT%H%M%S%fZ'))
        rows=[];errors=[];defined=specs(all_fee_manifest());supported=[];valid_policies=[]
        with transaction.atomic():
            courses=list(Course.objects.filter(university_id='UK007').order_by('course_id'))
            for c in courses:
                ts=list(Tuition.objects.filter(course=c,residency='INTL'))
                t=ts[0] if len(ts)==1 else None
                ss=list(TuitionSource.objects.filter(tuition=t)) if t else []
                source=ss[0] if len(ss)==1 else None
                error=fee_error(t,source) if t and len(ss)<=1 else 'Expected one INTL tuition and at most one source record.'
                reason=followups['routes'].get(c.course_id,{}).get('message',error)
                verified=bool(c.is_active and error is None)
                rows.append(dict(course_id=c.course_id,title=c.title,active=c.is_active,fee_verified=verified,
                    fee_reason=error,unconfirmed_message=reason if not verified else None,
                    academic_year=source.academic_year if verified else None,fee_basis=source.fee_basis if verified else None,
                    tuition=str(t.tuition_fee) if verified else None,duration=c.duration,start_month=c.start_month,start_year=c.start_year))
            valid={r['course_id'] for r in rows if r['fee_verified']};pending={r['course_id'] for r in rows if r['active'] and not r['fee_verified']}
            if len(courses)!=55 or valid!=set(all_fee_manifest()) or pending!=set(followups['routes']) or any(not c.is_active for c in courses):
                errors.append('Inventory differs from reviewed 55 active / 48 verified / seven pending state.')
            rules=list(ScholarshipRule.objects.filter(scholarship__university_id='UK007'))
            for key,spec in defined.items():
                matches=[]
                for rule in rules:
                    raw=rule.rules
                    if not isinstance(raw,dict):continue
                    digest=raw.get('evidence_sha256','')
                    if (rule.verified and rule.source_url==spec['source_url'] and rule.scholarship.name==spec['name']
                        and rule.scholarship.level==spec['level'] and {k:v for k,v in raw.items() if k!='evidence_sha256'}==make_rule(spec)
                        and len(digest)==64 and all(ch in '0123456789abcdef' for ch in digest)):
                        matches.append(rule)
                if len(matches)!=1:errors.append('Expected one canonical verified rule for '+key)
                else:supported.append((spec,matches[0]))
            policies=list(DepositPolicy.objects.filter(university_id='UK007'))
            for year in ['2026/27','2027/28']:
                matches=[p for p in policies if p.academic_year==year and p.verified and p.currency=='GBP' and p.source_url==FUNDING and p.rules==deposit_rule(year,all_fee_manifest())]
                if len(matches)!=1:errors.append('Expected one canonical deposit policy for '+year)
                else:valid_policies+=matches
            award_scope=set().union(*(set(s['course_ids']) for s,r in supported)) if supported else set()
            deposit_scope=set().union(*(set(p.rules['course_ids']) for p in valid_policies)) if valid_policies else set()
            numeric_scope=set().union(*(set(p.rules.get('mba_acceptance_deposits',{})) for p in valid_policies)) if valid_policies else set()
            summary=dict(total_course_records=len(courses),active_courses=sum(c.is_active for c in courses),inactive_courses=sum(not c.is_active for c in courses),
                verified_international_fees=len(valid),pending_international_fees=len(pending),
                verified_fee_bases=dict(Counter(r['fee_basis'] for r in rows if r['fee_verified'])),
                verified_fee_years=dict(Counter(r['academic_year'] for r in rows if r['fee_verified'])),
                scholarship_rule_records=len(rules),verified_scholarship_rule_records=sum(r.verified for r in rules),
                unverified_scholarship_rule_records=sum(not r.verified for r in rules),canonical_supported_rules=len(supported),
                verified_fee_courses_with_conditional_award_scope=len(valid & award_scope),
                verified_fee_courses_without_supported_award_scope=len(valid-award_scope),
                mba_course_records_with_scholarship_terms_pending=2,
                deposit_policy_records=len(policies),verified_deposit_policy_records=sum(p.verified for p in policies),
                verified_fee_courses_with_conditional_deposit_scope=len(valid & deposit_scope),
                courses_with_published_conditional_numeric_acceptance_deposit=len(valid & numeric_scope),
                confirmed_applicant_awards_inferred_from_audit=0)
            reviewed=set(previous['universities'])|{'UK007'}
            nxt=University.objects.filter(country='UK').exclude(university_id__in=reviewed).order_by('university_id').first()
            snapshot=export_school('UK007');next_data=export_school(nxt.university_id) if nxt else None
        report=dict(owner='aston_final_handoff_v1',university_id='UK007',university_name='Aston University',generated_at=generated.isoformat(),
            status='reviewed_with_outstanding_cases',database_changes=False,summary=summary,courses=rows,followups=followups,
            award_notices=notices(),user_reported_validation={'synthetic_award_deposit_api_scenarios_passed':960,'fee_routes_checked':48,'real_awards_confirmed':False},
            anomalies=errors,next_university_id=nxt.university_id if nxt else None,audit_path=str(folder/'aston-final-audit.json'))
        try:merged=merge_register(text,report)
        except ValueError as exc:raise CommandError(str(exc)) from exc
        combined=deepcopy(previous)
        combined['universities']['UK007']=dict(audit_path=report['audit_path'],audit_timestamp=generated.timestamp(),report=report)
        combined.update(updated_at=generated.isoformat(),database_changes=False)
        folder.mkdir(parents=True)
        (folder/'previous-progress-register.md').write_bytes(original_md);(folder/'previous-progress-register.json').write_bytes(original_json)
        write_json(folder/'aston-final-audit.json',report);write_json(folder/'aston-database-snapshot.json',snapshot)
        write_json(folder/'unconfirmed-course-and-mba-messages.json',followups)
        if next_data:write_json(folder/'next-uk-university-export.json',next_data)
        if errors:raise CommandError('Audit saved, combined register unchanged: '+'; '.join(errors))
        if md.read_bytes()!=original_md or jp.read_bytes()!=original_json:raise CommandError('Combined register changed during audit; rerun without overwriting newer work.')
        mdtemp=md.with_name(md.name+'.tmp');jtemp=jp.with_name(jp.name+'.tmp')
        mdtemp.write_text(merged,encoding='utf-8');jtemp.write_text(encoded(combined),encoding='utf-8')
        os.replace(mdtemp,md)
        try:os.replace(jtemp,jp)
        except OSError:
            md.write_bytes(original_md)
            raise
        write_json(root/'data/universities/progress/aston_latest_handoff.json',report)
        self.stdout.write(json.dumps(summary,indent=2))
        self.stdout.write('Aston audit: '+report['audit_path'])
        self.stdout.write('Existing combined Markdown/JSON updated; previous six university entries preserved.')
        self.stdout.write('Seven route reasons and MBA scholarship terms mismatch saved for later follow-up and comparison messages.')
        if nxt:self.stdout.write('Next university: '+nxt.university_id+' '+nxt.name+'; courses: '+str(len(next_data['courses'])))
        self.stdout.write('Reviewed with outstanding cases. No database records changed.')

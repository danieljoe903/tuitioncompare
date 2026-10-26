"""Read-only database audit, durable progress snapshot and next-school export."""
import json
import os
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from app.models import University, Course, Tuition, TuitionSource, Scholarship, ScholarshipRule, DepositPolicy
from app.scraping.uk.arden.audit import analyse, OWNER
from app.scraping.uk.arden.progress_register import update as update_cumulative

REVIEWED_IDS=['UK001','UK002','UK003','UK004']


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,path)


def export_school(uid):
    return dict(database_changes=False,university_id=uid,
        university=University.objects.filter(university_id=uid).values().get(),
        courses=list(Course.objects.filter(university_id=uid).order_by('course_id').values()),
        tuitions=list(Tuition.objects.filter(course__university_id=uid).values()),
        sources=list(TuitionSource.objects.filter(tuition__course__university_id=uid).values()),
        scholarships=list(Scholarship.objects.filter(university_id=uid).values()),
        scholarship_rules=list(ScholarshipRule.objects.filter(scholarship__university_id=uid).values()),
        deposit_policies=list(DepositPolicy.objects.filter(university_id=uid).values()))


def markdown(report,inventory,next_school):
    s=report['summary']
    lines=['# UK scraping progress — Arden handoff', '',
        'Generated: '+report['generated_at'], '',
        'Arden is reviewed with outstanding cases. This command made no database changes.', '',
        '| Metric | Courses |','|---|---:|']
    for key in ['total_course_records','active_courses','inactive_courses','verified_international_fees','pending_international_fees','verified_fee_courses_with_conditional_award_scope','verified_fee_courses_without_conditional_award_scope','verified_fee_courses_with_individually_advised_deposit_scope','verified_fee_online_courses_campus_deposit_policy_not_applicable']:
        lines.append('| '+key.replace('_',' ')+' | '+str(s[key])+' |')
    lines+=['','Verified numeric deposit amounts: 0. The campus amount is individually advised; online payment requirements remain unverified.','','## Outstanding tuition/mapping cases','','| Course | Reason | Next action |','|---|---|---|']
    for row in report['pending_fee_cases']:
        lines.append('| '+row['course_id']+' — '+row['title']+' | '+str(row['fee_review_reason']).replace('|','/')+' | '+str(row['next_action'] or 'Review current route, intake and fee evidence.').replace('|','/')+' |')
    lines+=['','## Verified-fee courses without award scope','','| Course | Saved review |','|---|---|']
    for row in report['verified_fee_scholarship_scope_gaps']:
        review=row.get('scholarship_review') or {}
        reason=review.get('reason') if isinstance(review,dict) else str(review)
        lines.append('| '+row['course_id']+' — '+row['title']+' | '+str(reason or 'Applicable scholarship deduction not verified.').replace('|','/')+' |')
    lines+=['','## UK inventory','','| ID | University | Workflow status |','|---|---|---|']
    for uni in inventory:
        status='Reviewed; see that university’s audit for unresolved cases' if uni['university_id'] in REVIEWED_IDS else 'Not marked reviewed in this workflow'
        lines.append('| '+uni['university_id']+' | '+uni['name'].replace('|','/')+' | '+status+' |')
    lines+=['','Next university: '+(next_school.university_id+' '+next_school.name if next_school else 'No unreviewed UK university found.'),'','## Audit limits','']
    lines+=['- '+n for n in report['notes']]
    if report['expected_count_differences'] or report['anomalies']:
        lines+=['','## Differences requiring review','',json.dumps(report['expected_count_differences'],indent=2)]
        lines+=['- '+n for n in report['anomalies']]
    return '\n'.join(lines)+'\n'


class Command(BaseCommand):
    help='Audit Arden saved fees/award/deposit coverage, record progress and export the next UK university. No database writes.'

    def add_arguments(self,parser):
        parser.add_argument('--next-university',help='Override next UK university ID, for example UK005. Must exist and be in the UK.')
        parser.add_argument('--strict',action='store_true',help='Return an error after saving reports if expected counts differ or consistency anomalies exist.')

    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        latest_path=root/'data/universities/progress/arden_latest_handoff.json'
        if latest_path.exists():
            try:old=json.loads(latest_path.read_text(encoding='utf-8'))
            except (ValueError,OSError) as exc:raise CommandError('Existing Arden progress record cannot be read; preserve it and review: '+str(exc))
            if old.get('owner')!=OWNER:
                raise CommandError('Existing progress file is not owned by this audit; no overwrite: '+str(latest_path))
        if not University.objects.filter(university_id='UK004').exists():
            raise CommandError('Arden UK004 not found.')
        data=export_school('UK004')
        # Separate international rows for coverage; preserve both residencies in the full export.
        reference_path=root/'data/universities/uk/arden/pending_19_review_reference.json'
        if not reference_path.is_file():raise CommandError('Missing review reference: '+str(reference_path))
        reference=json.loads(reference_path.read_text(encoding='utf-8'))
        report=analyse(data,reference)
        generated=timezone.now()
        folder=root/'logs/scraping-progress'/('arden-handoff-'+generated.strftime('%Y%m%dT%H%M%S%fZ'))
        inventory=list(University.objects.filter(country='UK').order_by('university_id').values('university_id','name','slug'))
        next_id=options.get('next_university')
        if next_id:
            next_school=University.objects.filter(country='UK',university_id=next_id).first()
            if next_school is None:raise CommandError('Requested next university is not an existing UK university.')
        else:
            next_school=University.objects.filter(country='UK').exclude(university_id__in=REVIEWED_IDS).order_by('university_id').first()
        report.update(generated_at=generated.isoformat(),database_changes=False,university_id='UK004',
            university_name=data['university']['name'],uk_inventory=inventory,reviewed_workflow_ids=REVIEWED_IDS,
            raw_arden_snapshot_path=str(folder/'arden-database-snapshot.json'),
            evidence_reference_path=str(reference_path),
            next_university_id=next_school.university_id if next_school else None)
        audit_path=folder/'arden-final-audit.json'
        register_path=folder/'uk-progress-register.md'
        write_json(audit_path,report)
        write_json(folder/'arden-database-snapshot.json',data)
        register_path.write_text(markdown(report,inventory,next_school),encoding='utf-8')
        next_path=None
        if next_school:
            next_data=export_school(next_school.university_id)
            next_data['generated_at']=generated.isoformat()
            next_path=folder/'next-uk-university-export.json'
            write_json(next_path,next_data)
        progress=dict(owner=OWNER,generated_at=generated.isoformat(),university_id='UK004',
            status=report['status'],database_changes=False,summary=report['summary'],
            pending_fee_cases=report['pending_fee_cases'],
            verified_fee_scholarship_scope_gaps=report['verified_fee_scholarship_scope_gaps'],
            audit_path=str(audit_path),progress_register_path=str(register_path),
            next_university_id=next_school.university_id if next_school else None,
            next_university_export=str(next_path) if next_path else None,
            expected_count_differences=report['expected_count_differences'],anomalies=report['anomalies'])
        write_json(latest_path,progress)
        try:
            cumulative_json,cumulative_md,missing=update_cumulative(root,report,audit_path,folder,write_json)
        except (ValueError,OSError) as exc:
            raise CommandError('Arden audit saved, but cumulative register update stopped: '+str(exc))
        self.stdout.write(json.dumps(report['summary'],indent=2))
        self.stdout.write('\nArden audit: '+str(audit_path))
        self.stdout.write('Cumulative progress register: '+str(cumulative_md))
        self.stdout.write('Cumulative JSON: '+str(cumulative_json))
        for uid in missing:
            self.stdout.write(self.style.WARNING(uid+': no saved coverage audit found; previous figures are not guessed.'))
        self.stdout.write('Latest Arden progress record: '+str(latest_path))
        if next_school:
            self.stdout.write('\nNext university: '+next_school.university_id+' '+next_school.name)
            self.stdout.write('Courses: '+str(len(next_data['courses'])))
            self.stdout.write('Scholarships: '+str(len(next_data['scholarships'])))
            self.stdout.write('Deposit policies: '+str(len(next_data['deposit_policies'])))
            self.stdout.write('Next university export: '+str(next_path))
        else:self.stdout.write('No unreviewed UK university found.')
        if report['expected_count_differences']:
            self.stdout.write(self.style.WARNING('Expected-count differences: '+json.dumps(report['expected_count_differences'])))
        for note in report['anomalies']:self.stdout.write(self.style.WARNING(note))
        self.stdout.write('\nAudit/export complete. Report files saved; no database records changed.')
        if options.get('strict') and (report['expected_count_differences'] or report['anomalies']):
            raise CommandError('Reports saved. Review expected-count differences/consistency anomalies before handoff.')

"""Read-only preview of the first 30 reviewed campus route/fee proposals."""
import hashlib
import json
import re
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.core.serializers.json import DjangoJSONEncoder
from app.models import Course,Tuition

DEFAULT='logs/aub/source-review-20261024T004000918202Z/source-review.json'
UG='https://aub.ac.uk/fees/undergraduate'
PG='https://aub.ac.uk/fees/postgraduate'
AWARDS='https://aub.ac.uk/international/international-scholarships/international-undergraduate-and-postgraduate-scholarships'
POLICY='https://webdocs.aub.ac.uk/Fees%20Payment%20Policy.pdf'


def money(value):
    n=Decimal(re.sub(r'[^0-9.]','',str(value)))
    if not n.is_finite() or n<=0:raise ValueError('Invalid published amount.')
    return n.quantize(Decimal('.01'))


def table_row(page,label,amount):
    # Match the entire intake label and both year/amount; never use the stale intro.
    hits=[]
    for table in page['tables']:
        for row in table:
            if len(row)>1 and row[0].strip()==label and money(row[1])==Decimal(amount):hits.append(row)
    if len(hits)!=1:raise ValueError('Cannot isolate one expected international row: '+label)
    return hits[0]


def plan_preview(data,manifest,current):
    pages={p['source_url']:p for p in data['pages']}
    ug=pages[UG];pg=pages[PG];awards=pages[AWARDS];pdf=pages[POLICY]
    table_row(ug,'2027/28 Applicants (all years)','19950.00')
    table_row(pg,'2027/28 Applicants (Full-time)','19950.00')
    table_row(pg,'2027/28 Master of Architecture Applicants','19950.00')
    if '2027/2028' not in awards['text'] or '£5,000' not in awards['text'] or '£3,000' not in awards['text']:
        raise ValueError('Scholarship cohort or published maximum changed.')
    if 'Fees Payment Policy 2026/27' not in ' '.join(pdf['text'].split()):
        raise ValueError('Expected the saved 2026/27 payment policy; review its cohort separately.')
    if len(manifest)!=30 or len({p['course_id'] for p in manifest})!=30:raise ValueError('Expected 30 reviewed route IDs.')
    proposals=[]
    snapshot={c['course_id']:c for c in data['database_snapshot']['courses']}
    for item in manifest:
        cid=item['course_id'];course=current[cid];page=pages[item['source_url']]
        if course['title']!=item['stored_title'] or course['title']!=snapshot[cid]['title'] or not course['is_active']:
            raise ValueError(cid+': title/status changed; review required.')
        if page['sha256']!=item['source_sha256'] or page['heading']!=item['source_title']:
            raise ValueError(cid+': source changed from the reviewed capture.')
        if '(Online)' in course['title'] or 'Foundation Year' in course['title']:
            raise ValueError(cid+': online/foundation route outside this campus batch.')
        validation='subject to validation' in page['text'].lower()
        if validation!=item['subject_to_validation']:raise ValueError(cid+': validation status changed.')
        maximum='5000.00' if course['level']=='UG' else '3000.00'
        code=re.search(r'Course code:\s*([A-Z0-9]+)\s*\(UCAS\)',page['text'])
        notes=['September 2027 / 2027/28 proposal; replacing the stored 2026 intake requires explicit review.',
               'Original gross tuition remains separate from competitive scholarship deductions.',
               'The stored 2026 scholarship deadline is not verified for this cohort.',
               '2026/27 payment-policy amounts have not been extended to 2027/28.']
        if validation:notes.append('This course is subject to validation; do not present admission/availability as unconditional.')
        if cid=='C_UK005_22':notes.append('MArch is two years; this academic-year quote is not a verified full two-year programme total. The fee table lists registration as N/A.')
        proposals.append(dict(course_id=cid,title=course['title'],mapping='reviewed_same_award_route',
            subject_to_validation=validation,before=dict(start_month=course['start_month'],start_year=course['start_year'],course_url=course['course_url']),
            proposed=dict(course_url=item['source_url'],start_month='September',start_year=2027,
                          academic_year='2027/28',gross_tuition='19950.00',currency='GBP',comparison_period='academic_year',
                          ucas_code_if_captured=code.group(1) if code else None),
            evidence=dict(course_page=item['source_url'],course_file=page['file'],course_sha256=page['sha256'],fee_page=UG if course['level']=='UG' else PG),
            scholarship=dict(status='competitive_actual_award_amount_required',published_maximum=maximum,
                illustrative_net_at_maximum=str(Decimal('19950.00')-Decimal(maximum)),automatic_deduction=False,
                source_url=AWARDS,questions_saved_for='scholarship_page',
                required_answers=['overseas_fee_status_confirmed','qualifying_offer_and_intake_confirmed','application_submitted_by_published_deadline','award_granted_and_accepted','confirmed_award_amount','no_disqualifying_other_AUB_award']),
            deposit=dict(numeric_amount=None,status='2027_28_policy_confirmation_required',message='Confirm the applicable acceptance and pre-CAS payment requirements with AUB; the collected payment policy is labelled 2026/27.'),
            messages=notes))
    return proposals


class Command(BaseCommand):
    help='Preview 30 reviewed AUB campus course/2027-28 fee proposals and scholarship/deposit caveats. No database writes.'
    def add_arguments(self,parser):parser.add_argument('--evidence',default=DEFAULT)
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);path=Path(options['evidence'])
        if not path.is_absolute():path=root/path
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('university_id')!='UK005':raise CommandError('Expected AUB UK005 evidence.')
        try:
            for page in data['pages']:
                name=page['file']
                if Path(name).name!=name:raise ValueError('Invalid capture filename.')
                content=(path.parent/name).read_bytes()
                if hashlib.sha256(content).hexdigest()!=page['sha256']:raise ValueError('Capture hash mismatch: '+name)
            manifest=json.loads((root/'data/universities/uk/aub/first_30_reviewed_routes.json').read_text(encoding='utf-8'))
            ids=[r['course_id'] for r in manifest]
            current={c['course_id']:c for c in Course.objects.filter(university_id='UK005',course_id__in=ids).values()}
            if set(current)!=set(ids):raise ValueError('Missing reviewed database courses.')
            proposals=plan_preview(data,manifest,current)
            for row in proposals:
                tuition=list(Tuition.objects.filter(course_id=row['course_id'],residency='INTL').values())
                if len(tuition)!=1:raise ValueError(row['course_id']+': expected one INTL tuition row.')
                row['before']['tuition']=tuition[0]
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            output=root/'logs/aub'/('first-30-preview-'+stamp+'.json')
            report=dict(generated_at=datetime.now(timezone.utc).isoformat(),university_id='UK005',database_changes=False,
                evidence_path=str(path),captures_hash_checked=len(data['pages']),proposals=proposals,
                summary=dict(campus_fee_proposals=30,validation_condition_cases=sum(r['subject_to_validation'] for r in proposals),
                             automatically_deducted_scholarships=0,verified_2027_28_numeric_deposit_amounts=0),
                outside_batch=['4 online MA programme totals','8 integrated foundation-year subject/UCAS routes','6 unmatched awards','Future Media Production fuzzy match is not a mapping','Digital Fashion Innovation versus Fashion Innovation requires explicit route review'])
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        except (ValueError,KeyError,OSError) as exc:raise CommandError(str(exc))
        for row in proposals:
            flag='; subject to validation' if row['subject_to_validation'] else ''
            self.stdout.write(row['course_id']+': GBP '+str(money(row['before']['tuition']['tuition_fee']))+' -> 19950.00; September 2027 / 2027/28'+flag)
        self.stdout.write(json.dumps(report['summary'],indent=2));self.stdout.write('Report: '+str(output))
        self.stdout.write('Preview only. No mappings applied or database records changed. Competitive awards require the actual granted amount; deposit cohort remains pending.')

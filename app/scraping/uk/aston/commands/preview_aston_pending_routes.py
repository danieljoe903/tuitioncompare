"""Preview targeted Aston corrections; never writes database records."""
import hashlib
import json
import re
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.timezone import now
from app.models import Course, Tuition

BASE = 'https://www.aston.ac.uk/study/courses/'
SPECS = [
    ('C_AST007_01', 'Global Business and Management (Top-up)', 'BSc (Hons) Global Business and Management (Top-up)', 'global-business-and-management-top-bsc/september-2027', '23500.00', '2027/28', 'September', 'annual_tuition', '1 Year', 'NN21', 'explicit_replacement_proposal'),
    ('C_UK007_26', 'Public Health', None, 'public-health-mph/september-2027', '24800.00', '2027/28', 'September', 'full_time_course_fee', '1 Year', None, 'reviewed_exact_full_time_route'),
    ('C_UK007_20', 'Advanced Computer Science', 'MSc Advanced Computer Science', 'advanced-computer-science-msc/january-2027', '24800.00', '2026/27', 'January', 'annual_tuition', '1 Year', None, 'explicit_replacement_proposal'),
    ('C_UK007_50', 'MBA', None, 'mba-full-time-mba/january-2027', '31500.00', '2026/27', 'January', 'annual_tuition', '1 Year', None, 'distinct_intake_confirmation'),
    ('C_UK007_24', 'MBA', None, 'mba-full-time-mba/september-2027', '31500.00', '2027/28', 'September', 'annual_tuition', '1 Year', None, 'existing_mba_intake_correction'),
]
PENDING = {
    'C_AST007_RES_01': 'MRes URL redirects to taught MSc; this does not confirm the stored research award, intake or fee.',
    'C_AST007_RES_02': 'Exact Health Sciences research route/intake and applicable fee remain unconfirmed.',
    'C_AST007_RES_03': 'MSc by Research is supported in research guidance, but exact stored route/intake and fee remain unconfirmed.',
    'C_AST007_RES_04': 'Research award regulations do not establish this exact route/intake and fee.',
    'C_UK007_36': 'Course URL returned 404; not evidence of course closure.',
    'C_UK007_37': 'Course URL returned 404; not evidence of course closure.',
    'C_UK007_47': 'External redirect stopped; exact route/intake and fee remain unconfirmed.',
}

def build_report(data, evidence_path, root, courses, tuitions, verify_raw=True):
    if data.get('university_id') != 'UK007' or data.get('database_changes') is not False:
        raise ValueError('Expected read-only UK007 pending11 evidence.')
    pages = {p['requested_url']: p for p in data['pages']}
    proposals, blocked = [], []
    for cid, heading, title, suffix, amount, year, month, basis, duration, ucas, mapping in SPECS:
        try:
            page = pages[BASE + suffix]
            if page.get('http_status') != 200 or page.get('source_url') != BASE + suffix:
                raise ValueError('Exact source URL was not captured successfully.')
            if heading.casefold() not in page.get('heading', '').casefold():
                raise ValueError('Unexpected award heading.')
            text = ' '.join(page['text'].split())
            label = 'Course Fee' if basis == 'full_time_course_fee' else 'Annual tuition fees?'
            price = format(int(float(amount)), ',')
            if not re.search(r'International students\s*\(' + re.escape(year) + r'\)\s*' + label + r':\s*£' + re.escape(price) + r'(?!\d)', text, re.I):
                raise ValueError('Exact international fee, year and fee label not established.')
            if month + ' 2027' not in text:
                raise ValueError('Selected intake not established.')
            if verify_raw:
                name = page['file']
                if Path(name).name != name:
                    raise ValueError('Unsafe capture filename.')
                raw = evidence_path.parent / name
                if hashlib.sha256(raw.read_bytes()).hexdigest() != page['sha256']:
                    raise ValueError('Raw source hash differs.')
            before = courses.get(cid)
            if before is None or before.get('university_id') != 'UK007':
                raise ValueError('Live Aston course missing.')
            rows = [t for t in tuitions if t['course_id'] == cid]
            if len(rows) > 1:
                raise ValueError('Multiple international tuition rows require review.')
            changes = dict(course_url=BASE + suffix, start_month=month, start_year=2027,
                           intake_months=month, duration=duration)
            if title: changes['title'] = title
            if ucas: changes['ucas_cao_code'] = ucas
            proposals.append(dict(course_id=cid, mapping=mapping, before_course=before,
                before_international_tuition=rows[0] if rows else None,
                requires_create_international_tuition=not rows, proposed_course=changes,
                tuition_fee=amount, currency='GBP', academic_year=year, fee_basis=basis,
                source_url=page['source_url'], source_sha256=page['sha256'], source_file=page['file'],
                requires_accept_replacements=bool(title), scholarship_scope_extended=False,
                deposit_scope_extended=False,
                applications_opening_soon='Applications opening soon' in text,
                fee_note=('Full-time MPH course fee; part-time price is not inferred.' if cid == 'C_UK007_26' else 'Published annual international tuition for the selected intake.'),
                deposit_note='Deposit amounts remain offer-letter specific. Any course-specific published payment requires separate reconciliation.'))
        except (KeyError, ValueError, OSError) as exc:
            blocked.append(dict(course_id=cid, reason=str(exc)))
    return dict(university_id='UK007', generated_at=now().isoformat(), evidence_path=str(evidence_path),
        database_changes=False, proposals=proposals, blocked=blocked,
        pending=[dict(course_id=k, reason=v) for k,v in PENDING.items()],
        summary=dict(fee_proposals=sum(p['course_id'] != 'C_UK007_24' for p in proposals),
                     existing_mba_intake_corrections=sum(p['course_id'] == 'C_UK007_24' for p in proposals),
                     pending_routes=7, blocked=len(blocked)),
        notes=['Preview only; replacements are not accepted.',
               'Keep both MBA records: January 2027 and September 2027 have separate fee-year evidence.',
               'MBA September page states applications opening soon.',
               'Scholarship deductions and deposit applicability require exact-cohort reconciliation; no new deduction or numeric deposit is assigned.',
               'Seven routes remain pending if all four new proposals are later accepted.'])

class Command(BaseCommand):
    help = 'Preview four Aston fee proposals and one existing MBA intake correction. No database writes.'
    def add_arguments(self, parser):
        parser.add_argument('--evidence', help='Path to pending11-investigation evidence.json; newest used by default.')
    def handle(self, *args, **options):
        root = Path(settings.BASE_DIR)
        if options.get('evidence'):
            path = Path(options['evidence'])
            if not path.is_absolute(): path = root / path
        else:
            files = sorted((root/'logs/aston').glob('pending11-investigation-*/evidence.json'))
            if not files: raise CommandError('No pending11 investigation evidence found.')
            path = files[-1]
        ids = [s[0] for s in SPECS]
        courses = {c['course_id']: c for c in Course.objects.filter(course_id__in=ids, university_id='UK007').values()}
        tuitions = list(Tuition.objects.filter(course_id__in=ids, residency='INTL').values())
        try:
            report = build_report(json.loads(path.read_text(encoding='utf-8-sig')), path, root, courses, tuitions)
        except (ValueError, KeyError, OSError) as exc: raise CommandError(str(exc)) from exc
        folder = root/'logs/aston'/('pending-routes-preview-'+now().strftime('%Y%m%dT%H%M%S%fZ'))
        folder.mkdir(parents=True)
        output = folder/'preview.json'
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        for p in report['proposals']:
            self.stdout.write(f"{p['course_id']}: GBP {p['tuition_fee']}; {p['start_month'] if 'start_month' in p else p['proposed_course']['start_month']} 2027 / {p['academic_year']}; {p['mapping']}")
            if p['requires_create_international_tuition']: self.stdout.write('  International tuition row must be created on a later reviewed import.')
            if p['applications_opening_soon']: self.stdout.write('  Applications opening soon.')
        for p in report['blocked']: self.stdout.write(p['course_id']+': BLOCKED — '+p['reason'])
        self.stdout.write(json.dumps(report['summary'], indent=2))
        self.stdout.write('Report: '+str(output))
        self.stdout.write('Preview only. No database records changed. Scholarship and deposit scopes unchanged.')
        if report['blocked']: raise CommandError('Blocked proposals require review; nothing applied.')

import hashlib,json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.utils.timezone import now
from app.scraping.uk.aston.remaining25 import plan,PENDING

class Command(BaseCommand):
    help='Review remaining25 Aston: preview14 fee routes, including one explicit MOptom replacement; hold11 identity/duplicate cases. No DB changes.'
    def add_arguments(self,p):p.add_argument('--evidence',help='Reindexed report; newest used by default.')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        if options.get('evidence'):
            path=Path(options['evidence']);path=path if path.is_absolute() else root/path
        else:
            files=sorted((root/'logs/aston').glob('source-review-*/reindexed-review-*.json'))
            if not files:raise CommandError('Reindexed Aston evidence not found.')
            path=files[-1]
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('university_id')!='UK007':raise CommandError('Expected UK007 evidence.')
        manifest=json.loads((root/'data/universities/uk/aston/reviewed_remaining14.json').read_text())
        original=Path(data['original_evidence_path']);original=original if original.is_absolute() else root/original
        pages={p['source_url']:p for p in data['pages']}
        for m in manifest:
            p=pages.get(m['source_url'])
            if not p or Path(p['file']).name!=p['file']:raise CommandError('Missing/unsafe source capture: '+m['course_id'])
            raw=original.parent/p['file']
            if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest()!=m['source_sha256']:
                raise CommandError('Raw capture hash differs: '+m['course_id'])
        report=plan(data,manifest)
        report['pending']=[dict(course_id=cid,status=status,reason=reason) for cid,(status,reason) in PENDING.items()]
        report['summary'].update(reviewed_remaining_records=25,existing_route_fee_proposals=sum(p['mapping']=='reviewed_exact_award_and_subject' for p in report['proposals']),
            explicit_replacement_proposals=sum(p['mapping']=='explicit_replacement_proposal' for p in report['proposals']),
            undergraduate_annual_fee_proposals=sum(p['level']=='UG' and p['fee_basis']=='annual_tuition' for p in report['proposals']),
            held_identity_duplicate_cases=len(PENDING))
        report.update(generated_at=now().isoformat(),evidence_path=str(path.resolve()),original_evidence_path=str(original),
            notes=['No database records changed. No replacement or duplicate retirement accepted.',
                   'MBA is excluded from reviewed Community/Cadbury schemes; MBA-specific awards still require review.',
                   'Medicine and proposed Optometry are excluded from reviewed Community/Cadbury schemes.',
                   'Other scholarship scopes must be extended only after exact-cohort reconciliation.',
                   'Deposit amounts remain offer-letter specific. This preview does not extend saved policy scopes.'])
        folder=root/'logs/aston'/('remaining25-preview-'+now().strftime('%Y%m%dT%H%M%S%fZ'));folder.mkdir(parents=True)
        output=folder/'preview.json';output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        for r in report['proposals']:
            self.stdout.write(r['course_id']+': GBP '+str(r['before_international_tuition']['tuition_fee'])+' -> '+r['tuition_fee']+'; '+r['academic_year']+'; '+r['fee_basis']+'; '+r['mapping'])
            if r.get('replacement_title'):self.stdout.write('  Explicit replacement: '+r['before_course']['title']+' -> '+r['replacement_title'])
            self.stdout.write('  Published duration: '+r['published_duration'])
        for r in report['pending']:self.stdout.write(r['course_id']+': '+r['status'])
        for r in report['blocked']:self.stdout.write(r['course_id']+': BLOCKED — '+r['reason'])
        self.stdout.write(json.dumps(report['summary'],indent=2));self.stdout.write('Report: '+str(output))
        self.stdout.write('Preview only. No database records changed. Scholarships and offer-letter deposit scopes require reconciliation.')
        if report['blocked']:raise CommandError('Blocked proposals saved for review; nothing applied.')

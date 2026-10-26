import hashlib,json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.utils.timezone import now
from app.scraping.uk.aston.first30 import plan

class Command(BaseCommand):
    help='Preview 30 exact Aston fee routes, intakes and published durations. No database writes.'
    def add_arguments(self,p):p.add_argument('--evidence',help='Reindexed report; newest used by default.')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        if options.get('evidence'):
            path=Path(options['evidence']);path=path if path.is_absolute() else root/path
        else:
            files=sorted((root/'logs/aston').glob('source-review-*/reindexed-review-*.json'))
            if not files:raise CommandError('Run reindex_aston_sources first.')
            path=files[-1]
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('university_id')!='UK007':raise CommandError('Expected Aston UK007 report.')
        manifest=json.loads((root/'data/universities/uk/aston/reviewed_first30.json').read_text())
        original=Path(data['original_evidence_path'])
        if not original.is_absolute():original=root/original
        pages={p['source_url']:p for p in data['pages']}
        for m in manifest:
            p=pages.get(m['source_url'])
            if not p or Path(p['file']).name!=p['file']:raise CommandError('Missing or unsafe source capture.')
            raw=original.parent/p['file']
            if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest()!=m['source_sha256']:
                raise CommandError('Raw source hash mismatch: '+m['course_id'])
        result=plan(data,manifest)
        result.update(generated_at=now().isoformat(),evidence_path=str(path.resolve()),original_evidence_path=str(original))
        folder=root/'logs/aston'/('first30-preview-'+now().strftime('%Y%m%dT%H%M%S%fZ'))
        folder.mkdir(parents=True);output=folder/'preview.json'
        output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
        for p in result['proposals']:
            self.stdout.write(p['course_id']+': GBP '+str(p['before_international_tuition']['tuition_fee'])+' -> '+p['tuition_fee']+'; '+p['start_month']+' 2027 / '+p['academic_year']+'; '+p['fee_basis'])
            if p['course_changes']:self.stdout.write('  Corrections: '+json.dumps(p['course_changes'],ensure_ascii=False))
        for b in result['blocked']:self.stdout.write(b['course_id']+': BLOCKED — '+b['reason'])
        self.stdout.write(json.dumps(result['summary'],indent=2))
        self.stdout.write('Report: '+str(output))
        self.stdout.write('Preview only. No database records changed. Scholarship and deposit scopes require separate reconciliation.')
        if result['blocked']:raise CommandError('Blocked proposals saved for review. Nothing applied.')

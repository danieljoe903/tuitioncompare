"""Reuse original captured evidence; no HTTP requests or database writes."""
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from django.utils.timezone import now
from app.scraping.uk.aston.reindex import repair

class Command(BaseCommand):
    help='Repair Aston suffix/prefix candidate matching using existing captures. No mappings approved or DB writes.'
    def add_arguments(self,p):p.add_argument('--evidence',help='Existing source-review.json; newest used by default.')
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR)
        if options.get('evidence'):
            path=Path(options['evidence']);path=path if path.is_absolute() else root/path
        else:
            files=sorted((root/'logs/aston').glob('source-review-*/source-review.json'))
            if not files:raise CommandError('No Aston source-review.json found.')
            path=files[-1]
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('university_id')!='UK007' or not data.get('pages') or not data.get('courses'):
            raise CommandError('Expected an Aston evidence report with captured pages and database courses.')
        rows,summary=repair(data)
        report={**data,'courses':rows,'summary':summary,'original_evidence_path':str(path.resolve()),
            'reindexed_at':now().isoformat(),'database_changes':False,'mappings_approved':False,
            'reindex_note':'Reused original captures; no source refreshed. Original errors and source hashes preserved. Relative capture files resolve beside original_evidence_path.'}
        output=path.parent/('reindexed-review-'+now().strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        self.stdout.write(json.dumps(summary,indent=2))
        self.stdout.write('Report: '+str(output))
        self.stdout.write('Original captures/errors retained. No mappings approved. No database records changed.')

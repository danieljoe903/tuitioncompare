"""Retain historical Painting record and audit its canonical course link."""
import json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils.timezone import now
from app.models import Course, Tuition, TuitionSource, TuitionUpdateAudit
from app.scraping.uk.aup.award_status import competitive_award_notices
from app.scraping.uk.aup.calculation import fee_error

EVIDENCE = 'https://www.aup.ac.uk/posts/layla-lyons-exploring-landscape-abstraction-and-the-creative-process-in-ma-fine-art'
OWNER = 'aup_painting_transition_v1'

class Command(BaseCommand):
    help = 'Preview/apply the documented Painting transition; report ended competitive award cycles.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        with transaction.atomic():
            old = Course.objects.select_for_update().get(university_id='UK006', course_id='C_UK006_24')
            target = Course.objects.select_for_update().get(university_id='UK006', course_id='C_UK006_17')
            if old.title != 'MA Painting' or target.title != 'MA Fine Art' or not target.is_active:
                raise CommandError('Course identity changed; review before applying.')
            target_fee = Tuition.objects.get(course=target, residency='INTL')
            source = TuitionSource.objects.filter(tuition=target_fee).first()
            if fee_error(target_fee, source):
                raise CommandError('Canonical MA Fine Art route/fee is not in the reviewed state.')
            tuition = Tuition.objects.get(course=old, residency='INTL')
            previous = TuitionUpdateAudit.objects.filter(tuition=tuition, course_identifier=old.course_id, residency='INTL')
            owned = any(isinstance(a.after, dict) and a.after.get('importer') == OWNER for a in previous)
            if not old.is_active and not owned:
                raise CommandError('Painting was already inactive without this transition audit; review its history.')
            if old.is_active and owned:
                raise CommandError('Painting was reactivated after this correction; review before changing it again.')
            change = old.is_active
            after = dict(importer=OWNER, is_active=False, canonical_course_id=target.course_id,
                         transition='MA Painting became part of MA Fine Art from September 2025',
                         source_url=EVIDENCE, reason='Retain historical record; avoid a second current MA Fine Art mapping.')
            if options['apply'] and change:
                old.is_active = False
                old.save(update_fields=['is_active', 'updated_at'])
                TuitionUpdateAudit.objects.create(tuition=tuition, course_identifier=old.course_id,
                    residency='INTL', before=dict(is_active=True, title=old.title), after=after)
            report = dict(university_id='UK006', generated_at=now().isoformat(),
                database_changes=bool(options['apply'] and change), changed_items=int(change),
                painting_transition=after, competitive_award_notices=competitive_award_notices(),
                unresolved_routes=['C_AUP006_01', 'C_UK006_18'],
                note='No tuition, numeric discounts, deposit policies or Alumni rules changed.')
            folder = Path(settings.BASE_DIR)/'logs/aup'/('route-award-status-'+now().strftime('%Y%m%dT%H%M%S%fZ'))
            folder.mkdir(parents=True, exist_ok=True)
            path = folder/'reconciliation.json'
            path.write_text(json.dumps(report, cls=DjangoJSONEncoder, indent=2), encoding='utf-8')
        self.stdout.write('Painting historical record -> MA Fine Art canonical C_UK006_17: '+('update' if change else 'unchanged'))
        self.stdout.write('Changed/proposed items: '+str(int(change)))
        self.stdout.write('Three published competitive award cycles: Has ended (deadline 25 February 2026); September 2027 availability unconfirmed.')
        self.stdout.write('Alumni rule retained; two route cases remain pending. Numeric tuition/deposits unchanged.')
        self.stdout.write('Report: '+str(path))
        self.stdout.write('Applied.' if options['apply'] else 'Preview only. No database records changed.')

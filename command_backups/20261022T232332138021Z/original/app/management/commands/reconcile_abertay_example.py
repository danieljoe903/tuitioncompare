"""One reviewed 2026/27 pilot record, preview by default."""
import json
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from app.models import Tuition, Scholarship, ScholarshipRule

FEE_SOURCE = 'https://www.abertay.ac.uk/study-apply/money-fees-and-funding/tuition-fees-2026-2027/'
AWARD_SOURCE = 'https://www.abertay.ac.uk/study-apply/money-fees-and-funding/scholarships/abertay-international-scholarship/'
TERMS_SOURCE = 'https://www.abertay.ac.uk/study-apply/money-fees-and-funding/terms-conditions/'

class Command(BaseCommand):
    help = 'Preview or reconcile Abertay course C_UK001_01 and scholarship 85 only.'
    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
    def handle(self, *args, **options):
        with transaction.atomic():
            tuition = Tuition.objects.select_for_update().select_related('course__university').get(pk=4703)
            award = Scholarship.objects.select_for_update().get(pk=85)
            if (tuition.course.course_id != 'C_UK001_01' or tuition.residency != 'INTL'
                or tuition.currency != 'GBP' or tuition.course.level != 'UG'
                or tuition.course.start_year != 2026
                or tuition.course.university.name != 'Abertay University'
                or award.university_id != tuition.course.university_id
                or award.name != 'Abertay International Scholarship' or award.level != 'UG'):
                raise CommandError('Pilot IDs do not match the reviewed course/award.')
            if tuition.tuition_fee not in (Decimal('15500'), Decimal('16950')) or tuition.scholarship != Decimal('3000'):
                raise CommandError('Amounts changed since review; stopped rather than overwrite.')
            if tuition.scholarship_source_id not in (None,85):
                raise CommandError('A different scholarship is linked.')
            rule = {
                'fee_basis_confirmed': True, 'benefit': 'tuition', 'kind': 'fixed',
                'value': '3000', 'currency': 'GBP', 'academic_year': '2026/27',
                'study_years': [1], 'percentage_basis': 'original_tuition',
                'award_status': 'conditional', 'stacking': 'exclusive', 'compatible_ids': [],
                'conditions': {'course_id':['C_UK001_01'], 'level':['UG'], 'fee_status':['INTL'],
                    'new_entrant':[True], 'full_time':[True], 'on_campus_dundee':[True],
                    'academic_conditions_met':[True], 'partner_discount':[False], 'repeat_year':[False]},
                'fee_source_url': FEE_SOURCE, 'terms_source_url': TERMS_SOURCE,
                'review_notes': 'Pilot annual deduction only. No combined awards or early-payment discounts. Award notification and acceptance still required.'
            }
            before = {'tuition_id':tuition.pk, 'tuition_fee':str(tuition.tuition_fee),
                      'scholarship':str(tuition.scholarship), 'scholarship_source_id':tuition.scholarship_source_id,
                      'scholarship_id':award.pk, 'deadline':str(award.deadline) if award.deadline else None,
                      'rule': list(ScholarshipRule.objects.filter(scholarship=award).values())}
            after = {'tuition_fee':'16950.00', 'legacy_scholarship':'3000.00',
                     'scholarship_source_id':85, 'deadline':None, 'rules':rule}
            self.stdout.write(json.dumps({'before':before,'proposed':after},indent=2,default=str))
            if not options['apply']:
                self.stdout.write('Preview only: no records changed. Use --apply after reviewing.')
                return
            folder = Path(settings.BASE_DIR)/'logs'/'scholarship-reconciliation'
            folder.mkdir(parents=True,exist_ok=True)
            backup = folder/(timezone.now().strftime('%Y%m%dT%H%M%S%f')+'.json')
            with backup.open('x',encoding='utf-8') as f:
                json.dump({'before':before,'proposed':after,'recorded_at':timezone.now().isoformat()},f,indent=2,default=str)
            tuition.tuition_fee = Decimal('16950')
            tuition.scholarship_source = award
            tuition.save(update_fields=['tuition_fee','scholarship_source'])
            # Official scholarship is assessed with course application; imported July deadline is unsupported.
            award.deadline = None
            award.save(update_fields=['deadline'])
            ScholarshipRule.objects.update_or_create(scholarship=award,defaults={
                'verified':True, 'source_url':AWARD_SOURCE,'reviewed_at':timezone.now(),'rules':rule})
            self.stdout.write(self.style.SUCCESS(f'Pilot reconciled. Before/after record: {backup}'))

"""Preview or save fixed early-payment rules without changing course fees."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.timezone import now
from app.models import Scholarship, ScholarshipRule, Course, Tuition
from app.scraping.uk.aub.early_payment import SOURCE, OWNER, SCOPES, make_rule
from app.scraping.uk.aub.remaining_calculation import fee_error


class Command(BaseCommand):
    help = 'Reconcile 2027/28 fixed early-payment waiver; no automatic eligibility assumption.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        if options['apply'] and not any('# AUB early-payment source guard v1' in p.read_text(encoding='utf-8-sig') for p in (Path(settings.BASE_DIR)/'app').rglob('*.py')):
            raise CommandError('Run connect_early_payment.py --apply first.')
        try:
            response = requests.get(SOURCE, timeout=(20,60))
            response.raise_for_status()
            if response.url.rstrip('/') != SOURCE:
                raise ValueError('Source redirected; review before applying.')
            text = ' '.join(BeautifulSoup(response.content, 'html.parser').stripped_strings)
            required = ['£1,500', '2027/2028', '1 August 2027', 'full annual fees', "can't be combined"]
            if not all(s in text for s in required) or 'Postgraduate (MA)' not in text:
                raise ValueError('Published amount, cohort or conditions changed.')
        except (requests.RequestException, ValueError) as exc:
            raise CommandError('Source verification stopped: '+str(exc)) from exc
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        folder = Path(settings.BASE_DIR)/'logs/aub'/('early-payment-'+stamp)
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'source.html').write_bytes(response.content)
        report = dict(university_id='UK005', source_url=SOURCE, source_sha256=hashlib.sha256(response.content).hexdigest(),
            checked_at=now().isoformat(), database_changes=False, rules=[])
        with transaction.atomic():
            plans = []
            for level, ids in SCOPES.items():
                for cid in sorted(ids):
                    courses = list(Course.objects.select_for_update().filter(university_id='UK005', course_id=cid))
                    rows = list(Tuition.objects.select_for_update().filter(course_id=cid, residency='INTL'))
                    if len(courses)!=1 or len(rows)!=1:
                        raise CommandError(cid+': expected one reviewed course and international tuition.')
                    c,t=courses[0],rows[0]
                    try: source=t.source_metadata
                    except AttributeError: source=None
                    if (not c.is_active or c.level!=level or c.start_year!=2027 or c.start_month!='September'
                        or fee_error(t,source,'annual') is not None):
                        raise CommandError(cid+': reviewed annual fee/intake guard failed.')
                name='AUB International Early Payment Waiver — '+level+' (2027/28)'
                matches=list(Scholarship.objects.select_for_update().filter(university_id='UK005',name=name))
                if len(matches)>1: raise CommandError('Duplicate managed waiver: '+name)
                award=matches[0] if matches else None
                rule=ScholarshipRule.objects.select_for_update().filter(scholarship=award).first() if award else None
                if award and (not rule or rule.rules.get('importer')!=OWNER):
                    raise CommandError('Existing award is not owned by this command.')
                desired=make_rule(level)
                changed=rule is None or rule.rules!=desired or not rule.verified or rule.source_url!=SOURCE
                plans.append((level,name,award,desired,changed))
                report['rules'].append(dict(name=name,course_count=len(ids),changed=changed,rules=desired,
                    before=rule.rules if rule else None))
            report['changed_items']=sum(p[-1] for p in plans)
            if options['apply']:
                for level,name,award,desired,changed in plans:
                    if not changed: continue
                    if award is None:
                        award=Scholarship.objects.create(university_id='UK005', name=name, level=level, currency='GBP',
                            award_type='Conditional early-payment waiver', award_value='1500.00', deadline=None, url=SOURCE,
                            eligibility='Accepted September 2027 offer, overseas fees, full annual payment received by 1 August 2027, no other AUB award.',
                            requirements='Confirmed payment facts saved on the scholarship page. No scholarship application or granted amount required.',
                            description='Fixed annual tuition waiver. Initial reviewed fee period only; not stacked with any other AUB award.')
                    ScholarshipRule.objects.update_or_create(scholarship=award, defaults=dict(verified=True, source_url=SOURCE, rules=desired, reviewed_at=now()))
                report['database_changes']=bool(report['changed_items'])
            (folder/'reconciliation.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        self.stdout.write('Reviewed annual scope: 39 courses (29 UG including 8 Year 0; 10 MA). MArch excluded pending exact scope confirmation.')
        self.stdout.write('Conditional GBP 19,950 - GBP 1,500 = GBP 18,450; confirmed full payment by 1 August 2027 and no other award required.')
        self.stdout.write('Changed/proposed items: '+str(report['changed_items']))
        self.stdout.write('Report: '+str(folder/'reconciliation.json'))
        self.stdout.write('Saved waiver rules.' if options['apply'] else 'Preview only. No database records changed.')
        self.stdout.write('Tuition, existing awards, deposits and course metadata unchanged. Six routes and campus 2027/28 deposits remain pending.')

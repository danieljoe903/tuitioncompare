"""Preview/apply the 34 source-identified Arden undergraduate routes."""
import calendar
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from bs4 import BeautifulSoup
from django.db import transaction
from django.utils import timezone as django_timezone
from app.models import Course, Tuition, TuitionSource, TuitionUpdateAudit, Scholarship, ScholarshipRule, DepositPolicy
from app.scraping.uk.arden import calculation as campus
from app.scraping.uk.arden import route_calculation as previous
from app.scraping.uk.arden.study_calculation import LEVELS_IMPORTER, ONLINE_TOPUP_IMPORTER, CAMPUS_IMPORTER, UNAVAILABLE_IMPORTER, eligibility_questions
from scripts.universities.uk.arden.import_campus_postgraduate import ROOT, BASE, YEAR, TERMS_URL, latest, read, canonical, source_state, common_conditions
from scripts.universities.uk.arden.preview_remaining_48 import check_evidence

IMPORTER = 'arden_remaining_rules_v3'
NUMBERS = [1,2,3,4,5,6,7,8,9,10,11,12,15,27,28,29,30,31,32,33,34,35,41,42,43,44,45,48,49,50,56,61,62,63]
IDS = {f'C_UK004_{n:02d}' for n in NUMBERS}
GLOBAL_LEVEL_IDS = [f'C_UK004_{n:02d}' for n in [1,2,3,5,6,9,10,41,42]]
ONLINE_TOPUPS = ['C_UK004_35', 'C_UK004_49', 'C_UK004_50']
CAMPUS_TOPUPS = ['C_UK004_27', 'C_UK004_28']
TERMS_TEXT_SHA256 = 'cec215ee99f62c34bc019bcf7b7ac2b512d8c9d74f5c650122097c414eac8207'


def make_specs():
    specs = []
    for location, value in [('Manchester', '3500.00'), ('London Stratford', '2500.00')]:
        conditions = common_conditions()
        conditions.update(course_id=CAMPUS_TOPUPS, arden_campus=[location])
        specs.append(dict(name=f'Arden reviewed UG top-ups v3 — {location} ({YEAR})', value=value,
                          conditions=conditions, family='campus_standard', campus=location))
    for intake in ['2026-11', '2027-02']:
        conditions = common_conditions()
        conditions.update(course_id=CAMPUS_TOPUPS, arden_campus=['London Stratford'], arden_intake=[intake],
                          arden_student_visa_sponsored=[True], arden_programme_at_least_120_credits=[True],
                          arden_unconditional_offer_on_time=[True], arden_deposit_paid_on_time=[True])
        specs.append(dict(name=f'Arden reviewed UG top-ups v3 — London early bird {intake} ({YEAR})', value='3000.00',
                          conditions=conditions, family='campus_early', campus='London Stratford'))
    for ids, gross, net, label in [(GLOBAL_LEVEL_IDS, '16500.00', '7425.00', 'full undergraduate programme'),
                                    (ONLINE_TOPUPS, '5500.00', '2475.00', 'Business Management top-up pathways')]:
        conditions = {key: [True] for key in previous.ONLINE_FACTS if key != 'arden_partner_route'}
        conditions.update(arden_partner_route=[False], course_id=ids, fee_status=['INTL'], arden_campus=['Online'],
                          arden_intake=['2027-01'], arden_online_tier2_country_confirmed=[True],
                          arden_online_gross_quote=[gross], arden_comparison_period=['total_programme'])
        specs.append(dict(name=f'Arden Global Tiers Tier 2 v3 — {label} ({YEAR})', value=str(Decimal(gross)-Decimal(net)),
                          conditions=conditions, family='online', campus='Online'))
    return specs


def rule_for(spec, specs):
    compatible = []
    if spec['family'] == 'campus_standard' and spec['campus'] == 'London Stratford':
        compatible = [str(s['object'].pk) for s in specs if s['family'] == 'campus_early']
    elif spec['family'] == 'campus_early':
        compatible = [str(s['object'].pk) for s in specs if s['family'] == 'campus_standard' and s['campus'] == 'London Stratford']
    return dict(importer=IMPORTER, kind='fixed', value=spec['value'], currency='GBP', benefit='tuition', academic_year=YEAR,
                study_years=[1], conditions=spec['conditions'], award_status='conditional',
                stacking='explicit' if compatible else 'exclusive', compatible_ids=compatible,
                percentage_basis='original_tuition', fee_basis_confirmed=True,
                comparison_period='total_programme' if spec['family'] == 'online' else 'annual',
                eligibility_questions=eligibility_questions(raw={'importer': ONLINE_TOPUP_IMPORTER}) if spec['family'] == 'online' else campus.eligibility_questions(),
                source_terms_text_sha256=TERMS_TEXT_SHA256,
                scope_note='Only the exact reviewed IDs, mode and intakes. Tier 2 net prices are programme-wide, exclusive and not allocated to individual levels. Prior credits, country proof and all other saved personal conditions remain required.',
                review_note='Manchester early-bird combination remains pending because the published figures conflict. Other regional tiers and Alumni offers are not inferred.')


def verify_policy(course_report_path):
    courses = read(course_report_path)
    path = Path(courses['source_review'])
    if not path.is_file():
        path = BASE / 'source-review-20261022T235952898295Z/source-review.json'
    sources = read(path)
    text = next(p['text'] for p in sources['pages'] if p['source_url'] == TERMS_URL)
    if hashlib.sha256(text.encode()).hexdigest() != TERMS_TEXT_SHA256:
        raise ValueError('Saved offer terms differ from the reviewed version; review before applying.')
    a = text.index('Arden University Global Tiers Offer 2026 - 2027 – Online Distance Learning')
    b = text.index('Terms and Conditions for Arden University’s UKI Super Early Bird Offer for September 2026 Intake', a)
    online = text[a:b]
    for token in ['£7,425.00', '£2,475.00', 'January 2027', 'Nigeria', 'Ghana',
                  'including themed routes/pathways, Top-Ups', 'OCTOBER 2026 INTAKE ONLY',
                  'BA (Hons) Business Management', 'BSc (Hons) Accounting and Finance',
                  'BSc (Hons) Psychology', 'BSc Healthcare & Management', 'LLB (Hons) Law']:
        if token not in online:
            raise ValueError('Missing Global Tiers evidence: ' + token)
    deposit = next(p for p in sources['pages'] if p['source_url'].endswith('/fees-for-international-students'))
    if 'You will be advised of the deposit value for your chosen course during your application.' not in deposit['text']:
        raise ValueError('Individually advised deposit evidence missing.')
    return path


def build_plan(item, page, captures, course_folder, fee_folder):
    cid = item['course_id']
    body = (course_folder / Path(page['html_file']).name).read_bytes()
    if hashlib.sha256(body).hexdigest() != item['source_html_sha256']:
        raise ValueError(cid + ': course HTML checksum mismatch.')
    soup = BeautifulSoup(body, 'html.parser')
    title = soup.find('input', attrs={'name': 'apply_now_course'}).get('value', '').replace('\u200b', '').strip()
    source_id = soup.find('input', attrs={'name': 'fees_course_id'}).get('value')
    if title != item['title'] or item['duration_evidence'] not in page['text']:
        raise ValueError(cid + ': title or duration does not match source.')
    selector = soup.find('select', attrs={'name': 'apply_now_course_study_type'})
    values = {o.get('value') for o in selector.find_all('option')} if selector else set()
    options = []
    for value in item['study_options']:
        match = re.fullmatch(r'c(\d+)\|t(dl1668|bl1666|bl1680)\|i(\d{4}-\d{2})\|m=(ft|pt)', value)
        if not match or match.group(1) != source_id or value not in values:
            raise ValueError(cid + ': source study-option mismatch.')
        options.append(dict(source_option=value, location={'dl1668':'Online','bl1666':'Manchester','bl1680':'London Stratford'}[match.group(2)],
                            intake=match.group(3), attendance='full_time' if match.group(4)=='ft' else 'part_time'))
    category = item['category']
    unavailable = category == 'published_fee_unavailable'
    online = category.startswith('online_')
    if not unavailable and not options:
        raise ValueError(cid + ': reviewed study options required.')
    if online and any(o['location'] != 'Online' or o['intake'] != '2027-01' for o in options):
        raise ValueError(cid + ': unreviewed online selection.')
    if category == 'campus_topup_fee_proposal' and (set(o['location'] for o in options) != {'Manchester','London Stratford'} or any(o['attendance'] != 'full_time' or o['intake'] not in {'2026-11','2027-02'} for o in options)):
        raise ValueError(cid + ': unreviewed campus selection.')
    texts = []
    if len(captures) != 2 or {r['country'] for r in captures} != {'Nigeria', 'Ghana'}:
        raise ValueError(cid + ': two country quotes required.')
    for capture in captures:
        data = (fee_folder / Path(capture['raw_response_file']).name).read_bytes()
        if hashlib.sha256(data).hexdigest() != capture['response_sha256'] or capture['request_parameters']['course_id'] != source_id:
            raise ValueError(cid + ': fee response integrity mismatch.')
        text = BeautifulSoup(json.loads(data)['data'], 'html.parser').get_text(' ', strip=True)
        if text != capture['text']:
            raise ValueError(cid + ': response text differs from evidence report.')
        texts.append(text)
    if texts[0] != texts[1]:
        raise ValueError(cid + ': country quotes differ.')
    text = texts[0]
    basis = 'unavailable' if unavailable else 'per_level' if category=='online_per_level_fee_proposal' else 'total_programme_fee' if online else 'annual_tuition'
    gross = None
    if unavailable:
        if 'Course fees are not available yet.' not in text:
            raise ValueError(cid + ': missing unpublished-fee evidence.')
    elif basis == 'per_level':
        if re.findall(r'Level ([456]) fee: £([\d,]+)', text) != [('4','5,500'),('5','5,500'),('6','5,500')]:
            raise ValueError(cid + ': unexpected level schedule.')
        gross = '5500.00'
    elif online:
        if re.findall(r'Studying Online Fee Total fee:\s*£([\d,]+(?:\.\d{2})?)', text) != ['5,500']:
            raise ValueError(cid + ': unexpected top-up total.')
        gross = '5500.00'
    else:
        for token in ['Manchester Campus Fee per year: £16,000','London, Stratford Campus Fee per year: £16,000',
                      'Fee with UK Offer for International Students: £12,500','Fee with UK Offer for International Students: £13,500']:
            if token not in text:
                raise ValueError(cid + ': missing campus quote.')
        gross = '16000.00'
    importer = UNAVAILABLE_IMPORTER if unavailable else LEVELS_IMPORTER if basis=='per_level' else ONLINE_TOPUP_IMPORTER if online else CAMPUS_IMPORTER
    # For unpublished fees, preserve the stored route details except the verified URL/title/duration.
    # All captured application choices are retained in raw evidence, not interpreted as international eligibility.
    after_course = dict(title=title, course_url=item['source_url'], duration=item['duration'])
    dates = sorted({o['intake'] for o in options})
    if not unavailable:
        primary = dates[0]
        year, month = map(int, primary.split('-'))
        after_course.update(location='Online' if online else 'London Stratford / Manchester', start_month=calendar.month_name[month],
                            start_year=year, intake_months=', '.join(dict.fromkeys(calendar.month_name[int(d[-2:])] for d in dates)))
    raw = dict(importer=importer, verified=not unavailable, academic_year=YEAR, currency='GBP', source_fee_category='International',
               course_kind='undergraduate_taught_online' if online else 'undergraduate_topup' if not unavailable else 'undergraduate_fee_pending',
               study_mode='online' if online else 'campus', study_options=options, default_intake=dates[0] if dates else None,
               course_source_url=item['source_url'], source_course_id=source_id, study_year=1,
               source_html_sha256=page['sha256'], fee_response_sha256=[r['response_sha256'] for r in captures],
               source_evidence_country_names=['Nigeria','Ghana'], published_duration_summary=item['duration_evidence'], duration_verified=True,
               academic_year_basis='Cohort 2026–2027 terms and January 2027 / November 2026–February 2027 advertised intakes; year derived using September–August convention, not printed as an academic year in the dynamic quote.',
               mapping={'kind':'accepted_route_correction','previous_title':item['stored_course']['title'],'published_title':title,'explicit_acceptance_required':True},
               deposit_status='campus_policy_not_applicable' if online else 'applicability_pending' if unavailable else 'application_advised')
    if unavailable:
        raw.update(verification_reason='The saved course fee response says fees are not available yet. An applicable international amount and intake remain unverified.',
                   fee_note='International tuition unavailable; no UK or related-route price substituted.', scholarship_status='pending_fee_verification',
                   academic_year_basis='Fee academic year unverified; 2026/27 is a review-cohort reference only, not a verified fee year.',
                   scholarship_review={'reason':'Scholarship applicability requires a verified fee, mode and intake.','eligibility_questions':[]},
                   deposit_review={'reason':'International route and deposit applicability require confirmation.','eligibility_questions':[]})
    elif online:
        raw.update(online_intakes=dates, fee_country_scope=['NG','GH'],
                   scholarship_status='verified_rule_eligibility_required' if cid in GLOBAL_LEVEL_IDS+ONLINE_TOPUPS else 'programme_scope_unconfirmed',
                   scholarship_review={'reason':'Global Tiers requires the complete programme basis; no per-level allocation is assumed.' if cid in GLOBAL_LEVEL_IDS else 'Verified Tier 2 top-up programme offer; all eligibility conditions required.' if cid in ONLINE_TOPUPS else 'No route-specific scholarship amount is confirmed; this does not establish that no scholarship exists.',
                                       'eligibility_questions':eligibility_questions(raw={'importer':importer})})
        if basis=='per_level':
            raw.update(fees_by_level={'4':'5500.00','5':'5500.00','6':'5500.00'},stored_fee_level='4',
                       programme_total_derivation={'operation':'sum','levels':['4','5','6'],'amount':'16500.00','separately_published_total':False},
                       fee_note='Published online tuition: £5,500 per level for Levels 4, 5 and 6. This is not annual tuition. Quotes verified for NG/GH only.')
        else:
            raw.update(total_programme_fee=gross,fee_note='Published £5,500 total online top-up programme fee; not annual tuition. Quotes verified for NG/GH only.')
    else:
        raw.update(annual_tuition=gross,campus_intakes={loc:sorted({o['intake'] for o in options if o['location']==loc}) for loc in ['Manchester','London Stratford']},
                   scholarship_status='verified_rule_eligibility_required',fee_note='Annual tuition for a one-year full-time campus top-up. Standard offers conditional; Manchester early-bird combination remains pending discrepancy review.',
                   scholarship_review={'reason':'Standard campus offers and London early-bird conditions verified. Manchester early-bird combination not applied.','eligibility_questions':campus.eligibility_questions()})
    source = dict(academic_year=YEAR, source_url=captures[0]['fee_source_url'], source_course_code=source_id, source_course_name=page['heading'], fee_basis=basis, raw_fees=raw)
    return after_course, gross, source


def run(*, apply=False, accept_route_corrections=False, preview_path=None):
    if apply and not accept_route_corrections:
        raise ValueError('Apply requires accept_route_corrections=True after reviewing the preview.')
    if '# Arden level fee integration v3' not in (ROOT/'app/views.py').read_text(encoding='utf-8-sig'):
        raise ValueError('Install connect_remaining_routes.py --apply before previewing this importer.')
    path = Path(preview_path) if preview_path else latest('remaining-48-preview-*.json')
    if not path.is_absolute():
        path = ROOT/path
    preview = read(path)
    if preview.get('blocked') or len(preview.get('proposals',[])) != 48 or preview.get('university_id') != 'UK004':
        raise ValueError('Expected the unblocked 48-course preview.')
    manifest = read(ROOT/'data/universities/uk/arden/remaining_48_manifest.json')
    manifest.update(evidence_course_report=preview['evidence_course_report'],evidence_fee_report=preview['evidence_fee_report'])
    check_evidence(ROOT, manifest)
    course_path = ROOT/manifest['evidence_course_report'];fee_path = ROOT/manifest['evidence_fee_report']
    pages = read(course_path)['pages']; fees = read(fee_path)['responses']
    policy_path = verify_policy(course_path)
    plan_data = read(ROOT/'data/universities/uk/arden/remaining_34_plan.json')['plans']
    if {p['course_id'] for p in plan_data} != IDS or len(plan_data)!=34:
        raise ValueError('Expected exactly the reviewed 34 source-identified courses.')
    preview_rows = {p['course_id']:p for p in preview['proposals']}
    computed=[]
    for item in plan_data:
        old=preview_rows[item['course_id']]
        if old['source_url'] != item['source_url'] or old['category'] != item['category']:
            raise ValueError('Source plan differs from reviewed preview: '+item['course_id'])
        page=next(p for p in pages if p['source_url']==item['source_url'])
        captures=[r for r in fees if r['course_page']==page['source_url']]
        computed.append((item,*build_plan(item,page,captures,course_path.parent,fee_path.parent)))
    report=dict(generated_at=django_timezone.now().isoformat(),mode='apply' if apply else 'preview',database_changes=False,
                preview_path=str(path),policy_evidence=str(policy_path),courses=[],rules=[],pending_identity_courses=14)
    now=django_timezone.now(); specs=make_specs(); writes=0
    with transaction.atomic():
        cq=Course.objects.filter(university_id='UK004',course_id__in=IDS)
        tq=Tuition.objects.filter(course__university_id='UK004',course_id__in=IDS,residency='INTL')
        sq=TuitionSource.objects.filter(tuition__course__university_id='UK004',tuition__course_id__in=IDS,tuition__residency='INTL')
        if apply:cq=cq.select_for_update();tq=tq.select_for_update();sq=sq.select_for_update()
        courses={c.course_id:c for c in cq};tuitions=list(tq);sources={s.tuition_id:s for s in sq};plans=[]
        for item,changes,gross,source in computed:
            cid=item['course_id'];course=courses.get(cid);matches=[t for t in tuitions if t.course_id==cid]
            if course is None or len(matches)!=1 or matches[0].currency!='GBP' or course.level!='UG':
                raise ValueError(cid+': expected one UG course and GBP international tuition.')
            tuition=matches[0];existing=sources.get(tuition.pk)
            if existing and existing.raw_fees.get('importer') not in {LEVELS_IMPORTER,ONLINE_TOPUP_IMPORTER,CAMPUS_IMPORTER,UNAVAILABLE_IMPORTER}:
                raise ValueError(cid+': another importer owns existing source metadata; no overwrite.')
            if not existing:
                baseline=preview_rows[cid]['current_database_course']
                for field in ['title','level','location','duration','start_month','start_year','course_url','is_active']:
                    if getattr(course,field)!=baseline[field]:raise ValueError(cid+': changed database field '+field)
                before_rows=preview_rows[cid]['current_international_tuitions']
                if len(before_rows)!=1 or Decimal(str(tuition.tuition_fee))!=Decimal(str(before_rows[0]['tuition_fee'])):
                    raise ValueError(cid+': tuition changed since preview.')
                if (Decimal(str(tuition.scholarship or 0)) != Decimal(str(before_rows[0].get('scholarship') or 0))
                        or tuition.scholarship_source_id != before_rows[0].get('scholarship_source_id')):
                    raise ValueError(cid+': stored award changed since preview; review before clearing it.')
            elif tuition.scholarship_source_id is not None or Decimal(str(tuition.scholarship or 0)) != 0:
                raise ValueError(cid+': a stored award was added after reconciliation; review before overwriting it.')
            if tuition.scholarship_source_id is not None:
                linked=ScholarshipRule.objects.filter(scholarship_id=tuition.scholarship_source_id,verified=True).first()
                if linked:raise ValueError(cid+': a verified linked award needs separate review; no deletion.')
            for field,value in changes.items():
                maximum=Course._meta.get_field(field).max_length
                if maximum and len(str(value))>maximum:raise ValueError(cid+': '+field+' exceeds model field length.')
            amount=str(Decimal(str(tuition.tuition_fee)).quantize(Decimal('.01')))
            before=canonical(dict(course={k:getattr(course,k) for k in changes},tuition=dict(tuition_fee=amount,scholarship=str(tuition.scholarship or 0),scholarship_source_id=tuition.scholarship_source_id),source=source_state(existing)))
            after=canonical(dict(course=changes,tuition=dict(tuition_fee=gross if gross is not None else amount,scholarship='0',scholarship_source_id=None),source=source))
            changed=before!=after;plans.append((course,tuition,changes,source,before,after,changed))
            report['courses'].append(dict(course_id=cid,changed=changed,before=before,after=after))
            print(f"{cid}: {source['fee_basis']}; GBP {amount} -> {after['tuition']['tuition_fee']}; {'update' if changed else 'unchanged'}" if gross else f"{cid}: international fee unavailable; numeric tuition preserved; {'update' if changed else 'unchanged'}")
        pq=DepositPolicy.objects.filter(university_id='UK004',academic_year=YEAR)
        if apply:pq=pq.select_for_update()
        policy=pq.first()
        if not policy or not policy.verified or policy.rules.get('kind')!='application_advised' or policy.rules.get('importer')!=campus.IMPORTER:
            raise ValueError('Expected the previously verified Arden deposit policy.')
        new_policy={**policy.rules,'course_ids':sorted(set(policy.rules.get('course_ids',[]))|set(CAMPUS_TOPUPS))}
        report['deposit_before']=canonical(policy.rules);report['deposit_after']=canonical(new_policy)
        for spec in specs:
            query=Scholarship.objects.filter(university_id='UK004',name=spec['name'])
            if apply:query=query.select_for_update()
            rows=list(query)
            if len(rows)>1:raise ValueError('Duplicate managed scholarship: '+spec['name'])
            spec['object']=rows[0] if rows else None
            old_rule=ScholarshipRule.objects.filter(scholarship=rows[0]).first() if rows else None
            if rows and (not old_rule or old_rule.rules.get('importer')!=IMPORTER):raise ValueError('Unowned scholarship: '+spec['name'])
            spec['before_rule']=canonical(old_rule.rules) if old_rule else None
            if rows and (rows[0].level!='UG' or rows[0].currency!='GBP' or Decimal(str(rows[0].award_value))!=Decimal(spec['value'])):
                raise ValueError('Managed scholarship changed: '+spec['name'])
        if apply:
            for spec in specs:
                if spec['object'] is None:
                    spec['object']=Scholarship.objects.create(university_id='UK004',name=spec['name'],level='UG',currency='GBP',award_type='Fixed tuition reduction',award_value=spec['value'],deadline=None,url=TERMS_URL,
                        eligibility='Published scope verified; all saved personal conditions required.',requirements='Confirmed mode, intake, offer and country evidence; unknown answers block deductions.',
                        description='Programme-wide Global Tiers reduction or campus one-year reduction; never an unverified per-level allocation.')
                    writes+=1
        # Preview uses stable symbolic IDs for not-yet-created records; apply uses actual database IDs.
        for i,spec in enumerate(specs):
            if spec['object'] is None:
                from types import SimpleNamespace
                spec['object']=SimpleNamespace(pk='preview-'+str(i))
        rule_changes=False
        for spec in specs:
            rule=rule_for(spec,specs);changed=rule!=spec['before_rule']
            old=ScholarshipRule.objects.filter(scholarship_id=spec['object'].pk).first() if not str(spec['object'].pk).startswith('preview-') else None
            changed=changed or bool(old and (not old.verified or old.source_url!=TERMS_URL))
            report['rules'].append(dict(name=spec['name'],changed=changed,before=spec['before_rule'],after=rule));rule_changes |= changed
            if apply and changed:
                ScholarshipRule.objects.update_or_create(scholarship=spec['object'],defaults=dict(verified=True,source_url=TERMS_URL,rules=rule,reviewed_at=now));writes+=1
        policy_changed=policy.rules!=new_policy
        if apply and policy_changed:
            policy.rules=new_policy;policy.checked_at=now;policy.save(update_fields=['rules','checked_at','updated_at']);writes+=1
        if apply:
            for course,tuition,changes,source,before,after,changed in plans:
                if not changed:continue
                for field,value in changes.items():setattr(course,field,value)
                course.save(update_fields=[*changes,'updated_at'])
                tuition.tuition_fee=Decimal(after['tuition']['tuition_fee']);tuition.scholarship=0;tuition.scholarship_source_id=None
                tuition.save(update_fields=['tuition_fee','scholarship','scholarship_source'])
                TuitionSource.objects.update_or_create(tuition=tuition,defaults=source)
                TuitionUpdateAudit.objects.create(tuition=tuition,course_identifier=course.course_id,residency='INTL',before=before,after=after);writes+=1
            if (rule_changes or policy_changed) and not any(p[-1] for p in plans):
                # Audit rule/policy-only reconciliation as well, with no fabricated fee change.
                TuitionUpdateAudit.objects.create(tuition=plans[0][1],course_identifier=plans[0][0].course_id,residency='INTL',
                    before={'rules':[{'name':r['name'],'rules':r['before']} for r in report['rules']],'deposit':report['deposit_before']},
                    after={'rules':[{'name':r['name'],'rules':r['after']} for r in report['rules']],'deposit':report['deposit_after']})
        report.update(course_changes=sum(p[-1] for p in plans),managed_rule_records=len(specs),deposit_scope=len(new_policy['course_ids']),
                      rules_changed=sum(r['changed'] for r in report['rules']),deposit_changed=policy_changed,
                      database_changes=bool(writes),changed_items=writes,status='transaction_prepared' if apply else 'preview')
        output=BASE/('remaining-34-import-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    report['status']='applied' if apply else 'preview';output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Course changes:',report['course_changes']);print('Managed scholarship rules:',len(specs));print('Campus deposit scope:',report['deposit_scope'])
    print('Verified fee schedules: 27. Unpublished fees: 7. Identity/duplicate reviews retained: 14.')
    print('Full-programme Tier 2 scope: 9 degrees and 3 online top-ups. Other programme scopes remain pending.')
    print('Report:',output);print('Applied reviewed routes, fees, rules and review messages.' if apply else 'Preview only. No database records changed.')
    return output

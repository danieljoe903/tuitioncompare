"""Preview/apply the explicitly reviewed Criminology and Accounting foundation replacements."""
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from app.models import Course, Tuition, TuitionSource, TuitionUpdateAudit, ScholarshipRule
from app.scraping.uk.arden.study_calculation import LEVELS_IMPORTER, UNAVAILABLE_IMPORTER, eligibility_questions

ROOT=Path(settings.BASE_DIR)
IDS={'C_UK004_46','C_UK004_57'}
BATCH='arden_two_explicit_replacements_v5'
DEFAULT='logs/arden/twelve-route-review-20261023T133816761598Z.json'
TERMS_TEXT_SHA256='57a7da5cfd57b3290393dc67e8b12c8e0bfc2c6a41657f3a8f60018022fb098b'
OLD_TITLES={'C_UK004_46':'BA (Hons) Criminal Justice','C_UK004_57':'BA (Hons) Business Management (Accounting and Finance) with Foundation Year'}
URLS={
    'C_UK004_46':'https://arden.ac.uk/our-courses/undergraduate/criminology-degree/ba-hons-criminology',
    'C_UK004_57':'https://arden.ac.uk/our-courses/undergraduate/foundation-year-entry-degrees/bsc-hons-accounting-and-finance-foundation',
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def canonical(value):
    return json.loads(json.dumps(value,cls=DjangoJSONEncoder))


def checked(folder,filename,sha):
    data=(folder/Path(filename).name).read_bytes()
    if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('Source checksum mismatch: '+filename)
    return data


def source_state(source):
    if source is None:return None
    return {key:getattr(source,key) for key in ['academic_year','source_url','source_course_code','source_course_name','fee_basis','raw_fees']}


def money(value):
    amount=Decimal(str(value or 0))
    if not amount.is_finite() or amount<0:raise ValueError('A stored monetary amount is invalid.')
    return str(amount.quantize(Decimal('.01')))


def evidence_plans(preview,path):
    current=read(ROOT/'logs/arden/pending-21-investigation-20261023T095806836678Z/pending-21-evidence.json')
    folder=ROOT/'logs/arden/pending-21-investigation-20261023T095806836678Z'
    course_path=ROOT/'logs/arden/course-evidence-20261023T001908157007Z/course-evidence.json'
    fee_path=ROOT/'logs/arden/dynamic-fees-20261023T010111265891Z/dynamic-fees.json'
    old_courses=read(course_path);old_fees=read(fee_path)
    terms=next(p for p in current['pages'] if p['source_url']=='https://arden.ac.uk/terms-and-conditions')
    checked(folder,terms['file'],terms['sha256'])
    if hashlib.sha256(terms['text'].encode()).hexdigest()!=TERMS_TEXT_SHA256:
        raise ValueError('Collected scholarship terms differ from the reviewed version.')
    start=terms['text'].index('Arden University Global Tiers Offer 2026 - 2027 – Online Distance Learning')
    a=terms['text'].index('Schedule 1 – Eligible Programmes',start);b=terms['text'].index('Schedule 2 –',a)
    if 'Criminology' in terms['text'][a:b] or 'Criminal Justice' in terms['text'][a:b]:
        raise ValueError('Global Tiers programme scope changed; review it before applying.')
    proposals={p['course_id']:p for p in preview['replacement_proposals']}
    plans=[]
    for cid in sorted(IDS):
        item=proposals[cid];url=URLS[cid];is_crim=cid=='C_UK004_46'
        if item['kind']!='explicit_replacement_proposal' or item['before']['title']!=OLD_TITLES[cid] or item['after']['course_url']!=url:
            raise ValueError(cid+': replacement preview does not match the reviewed proposal.')
        if is_crim:
            matches=[p for p in current['pages'] if p['source_url']==url]
            if len(matches)!=1 or matches[0]['http_status']!=200 or matches[0]['redirects']:
                raise ValueError('A direct, successful Criminology capture is required.')
            page=matches[0];data=checked(folder,page['file'],page['sha256'])
            captures=[r for r in current['fees'] if r['course_page']==url]
        else:
            page=next(p for p in old_courses['pages'] if p['source_url']==url)
            data=checked(course_path.parent,page['html_file'],page['sha256'])
            captures=[r for r in old_fees['responses'] if r['course_page']==url]
        soup=BeautifulSoup(data,'html.parser')
        title=soup.find('input',attrs={'name':'apply_now_course'}).get('value','').strip()
        source_id=soup.find('input',attrs={'name':'fees_course_id'}).get('value')
        expected_title='BA (Hons) Criminology' if is_crim else 'BSc (Hons) Accounting and Finance with Foundation Year'
        if title!=expected_title or item['after']['title']!=title:raise ValueError(cid+': published title mismatch.')
        if len(captures)!=2 or {r['country'] for r in captures}!={'Nigeria','Ghana'}:raise ValueError(cid+': NG/GH captures required.')
        hashes=[]
        for capture in captures:
            if is_crim:raw=checked(folder,capture['file'],capture['sha256']);sha=capture['sha256']
            else:raw=checked(fee_path.parent,capture['raw_response_file'],capture['response_sha256']);sha=capture['response_sha256']
            if capture['request_parameters']['course_id']!=source_id:raise ValueError(cid+': response course code mismatch.')
            text=BeautifulSoup(json.loads(raw)['data'],'html.parser').get_text(' ',strip=True)
            if text!=capture['text']:raise ValueError(cid+': response text differs from evidence.')
            if is_crim and re.findall(r'Level ([456]) fee: £([\d,]+)',text)!=[('4','5,500'),('5','5,500'),('6','5,500')]:
                raise ValueError('Criminology fee schedule changed.')
            if not is_crim and 'Course fees are not available yet.' not in text:
                raise ValueError('Foundation fee availability changed; review the new amount.')
            hashes.append(sha)
        changes=dict(title=title,duration='3 years+ (part-time)' if is_crim else '4 Years',course_url=url)
        raw=dict(importer=LEVELS_IMPORTER if is_crim else UNAVAILABLE_IMPORTER,reconciliation_batch=BATCH,
                 verified=is_crim,academic_year='2026/27' if is_crim else '',currency='GBP',source_fee_category='International',
                 course_source_url=url,source_course_id=source_id,source_html_sha256=page['sha256'],fee_response_sha256=hashes,
                 study_year=1,source_terms_text_sha256=TERMS_TEXT_SHA256,
                 mapping=dict(kind='explicit_replacement',previous_title=OLD_TITLES[cid],published_title=title,
                              reviewed_preview_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),acceptance_required=True,accepted_on_apply=True))
        if is_crim:
            expected=f'c{source_id}|tdl1668|i2027-01|m=pt'
            selector=soup.find('select',attrs={'name':'apply_now_course_study_type'})
            options={o.get('value') for o in selector.find_all('option')} if selector else set()
            if expected not in options or 'Part-time: 3 years+ (Online)' not in page['text']:
                raise ValueError('Criminology intake or duration missing.')
            changes.update(location='Online',start_month='January',start_year=2027,intake_months='January')
            raw.update(course_kind='undergraduate_taught_online',study_mode='online',fees_by_level={'4':'5500.00','5':'5500.00','6':'5500.00'},stored_fee_level='4',
                       programme_total_derivation={'operation':'sum','levels':['4','5','6'],'amount':'16500.00','separately_published_total':False},
                       fee_country_scope=['NG','GH'],online_intakes=['2027-01'],default_intake='2027-01',
                       study_options=[dict(source_option=expected,location='Online',intake='2027-01',attendance='part_time')],
                       academic_year_basis='Derived from advertised January 2027 intake under the September–August convention, not a printed fee-year label.',
                       fee_note='Published online tuition £5,500 per level for Levels 4–6, not annual tuition. January 2027 NG/GH quotes reviewed. No verified scholarship deduction currently applies to this course.',
                       scholarship_status='not_in_collected_global_tiers_schedule_1',deposit_status='campus_policy_not_applicable',
                       scholarship_review={'reason':'Criminology is absent from the collected Global Tiers eligible-programme list. No deduction from that offer is approved. Other awards remain unconfirmed.',
                                           'eligibility_questions':eligibility_questions(raw={'importer':LEVELS_IMPORTER})},
                       deposit_review={'reason':'Campus pre-CAS deposit policy does not apply to online study. Confirm any online payment requirements separately.','eligibility_questions':[]})
        else:
            if 'Full-time: 4 years (Blended)' not in page['text']:raise ValueError('Foundation duration not confirmed.')
            changes.update(start_month='',intake_months='')
            if Course._meta.get_field('start_year').null:changes['start_year']=None
            raw.update(course_kind='undergraduate_foundation_fee_pending',study_mode='campus',
                       academic_year_basis='Applicable international intake and fee year unverified; no academic year assigned.',
                       verification_reason='The BSc Accounting and Finance with Foundation Year route is identified, but the captured international fee is unavailable. Intake and scholarship applicability need confirmation.',
                       fee_note='International tuition unavailable for this replacement foundation route; no home or non-foundation price substituted.',
                       scholarship_status='pending_international_fee_intake_and_scope',deposit_status='pending_international_route_confirmation',
                       scholarship_review={'reason':'An applicable international fee, intake and published award scope must be confirmed before any deduction.','eligibility_questions':[]},
                       deposit_review={'reason':'Deposit policy applicability for this international foundation route is unconfirmed.','eligibility_questions':[]})
        source=dict(academic_year=raw['academic_year'],source_url=captures[0]['source_url'] if is_crim else captures[0]['fee_source_url'],
                    source_course_code=source_id,source_course_name=page['heading'],fee_basis='per_level' if is_crim else 'unavailable',raw_fees=raw)
        plans.append(dict(course_id=cid,changes=changes,source=source,gross='5500.00' if is_crim else None))
    return plans


def run(*,apply=False,accept_replacements=False,preview_path=DEFAULT):
    if apply and not accept_replacements:raise ValueError('Review first; applying requires accept_replacements=True.')
    if '# Arden level fee integration v3' not in (ROOT/'app/views.py').read_text(encoding='utf-8-sig'):
        raise ValueError('The tested Arden level-fee comparison connector must be installed first.')
    path=Path(preview_path)
    if not path.is_absolute():path=ROOT/path
    preview=read(path)
    if preview.get('database_changes') is not False or len(preview.get('replacement_proposals',[]))!=2 or {p['course_id'] for p in preview['replacement_proposals']}!=IDS:
        raise ValueError('Expected the reviewed two-replacement preview.')
    plans=evidence_plans(preview,path)
    baselines={p['course_id']:p['before'] for p in preview['replacement_proposals']}
    report=dict(generated_at=datetime.now(timezone.utc).isoformat(),mode='apply' if apply else 'preview',database_changes=False,
                accepted_replacements=bool(apply and accept_replacements),preview_path=str(path),courses=[],scholarship_rules_changed=False,deposit_policies_changed=False)
    with transaction.atomic():
        cq=Course.objects.filter(university_id='UK004',course_id__in=IDS)
        tq=Tuition.objects.filter(course__university_id='UK004',course_id__in=IDS,residency='INTL')
        sq=TuitionSource.objects.filter(tuition__course__university_id='UK004',tuition__course_id__in=IDS,tuition__residency='INTL')
        if apply:cq=cq.select_for_update();tq=tq.select_for_update();sq=sq.select_for_update()
        courses={c.course_id:c for c in cq};tuitions=list(tq);sources={s.tuition_id:s for s in sq};writes=[]
        for plan in plans:
            cid=plan['course_id'];course=courses.get(cid);rows=[t for t in tuitions if t.course_id==cid]
            if course is None or not course.is_active or course.level!='UG' or len(rows)!=1 or rows[0].currency!='GBP':
                raise ValueError(cid+': active UG course and one GBP international tuition required.')
            tuition=rows[0];source=sources.get(tuition.pk)
            if source and source.raw_fees.get('reconciliation_batch')!=BATCH:
                raise ValueError(cid+': another source reconciliation exists; no overwrite.')
            if not source:
                for field,value in baselines[cid].items():
                    if getattr(course,field)!=value:raise ValueError(cid+': course changed since preview: '+field)
                snapshots=[t for t in preview['current_international_tuitions'] if t['course_id']==cid]
                if len(snapshots)!=1:raise ValueError(cid+': missing tuition preview snapshot.')
                snapshot=snapshots[0]
                for field in ['tuition_fee','scholarship','tuition_deposit']:
                    if money(getattr(tuition,field))!=money(snapshot[field]):raise ValueError(cid+': '+field+' changed since preview.')
                if tuition.scholarship_source_id!=snapshot.get('scholarship_source_id'):raise ValueError(cid+': linked award changed since preview.')
            else:
                for field,value in plan['changes'].items():
                    if getattr(course,field)!=value:raise ValueError(cid+': accepted route was changed after reconciliation; review it again.')
                if plan['gross'] is not None and (money(tuition.tuition_fee)!=plan['gross'] or money(tuition.scholarship)!='0.00' or tuition.scholarship_source_id is not None):
                    raise ValueError(cid+': price or stored award changed after reconciliation; no overwrite.')
            for field,value in plan['changes'].items():
                maximum=Course._meta.get_field(field).max_length
                if maximum and value is not None and len(str(value))>maximum:raise ValueError(cid+': model field too short: '+field)
            if Course.objects.filter(university_id='UK004',is_active=True,title__iexact=plan['changes']['title']).exclude(course_id=cid).exists():
                raise ValueError(cid+': proposed replacement already exists as another active record.')
            if cid=='C_UK004_46':
                if tuition.scholarship_source_id is not None and ScholarshipRule.objects.filter(scholarship_id=tuition.scholarship_source_id,verified=True).exists():
                    raise ValueError('A verified linked award requires separate review before clearing a stored deduction.')
                for rule in ScholarshipRule.objects.filter(scholarship__university_id='UK004',verified=True):
                    scope=rule.rules.get('conditions',{}).get('course_id')
                    if scope is None or cid in scope:
                        raise ValueError('A saved verified award may cover the replacement. Review its scope before importing.')
            before_money=dict(tuition_fee=money(tuition.tuition_fee),scholarship=money(tuition.scholarship),scholarship_source_id=tuition.scholarship_source_id,
                              tuition_deposit=money(tuition.tuition_deposit) if tuition.tuition_deposit is not None else None)
            after_money={**before_money}
            if plan['gross'] is not None:after_money.update(tuition_fee=plan['gross'],scholarship='0.00',scholarship_source_id=None)
            before=canonical(dict(course={field:getattr(course,field) for field in plan['changes']},tuition=before_money,source=source_state(source)))
            after=canonical(dict(course=plan['changes'],tuition=after_money,source=plan['source']))
            changed=before!=after;writes.append((course,tuition,plan,before,after,changed))
            report['courses'].append(dict(course_id=cid,kind='explicit_replacement',changed=changed,before=before,after=after))
            print(cid+': '+plan['changes']['title']+'; '+plan['source']['fee_basis']+'; '+('update' if changed else 'unchanged'))
        count=0
        if apply:
            for course,tuition,plan,before,after,changed in writes:
                if not changed:continue
                for field,value in plan['changes'].items():setattr(course,field,value)
                course.save(update_fields=[*plan['changes'],'updated_at'])
                if plan['gross'] is not None:
                    tuition.tuition_fee=Decimal(plan['gross']);tuition.scholarship=0;tuition.scholarship_source_id=None
                    tuition.save(update_fields=['tuition_fee','scholarship','scholarship_source'])
                TuitionSource.objects.update_or_create(tuition=tuition,defaults=plan['source'])
                TuitionUpdateAudit.objects.create(tuition=tuition,course_identifier=course.course_id,residency='INTL',before=before,after=after)
                count+=1
        report.update(proposed_changes=sum(w[-1] for w in writes),applied_changes=count,database_changes=bool(count),status='transaction_prepared' if apply else 'preview')
        output=ROOT/'logs/arden'/('two-replacements-import-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    report['status']='applied' if apply else 'preview';output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Changed/proposed records:',report['proposed_changes'])
    print('Criminology: GBP 5500 per level; derived full programme GBP 16500. No verified scholarship deduction.')
    print('Accounting foundation: route replaced only; international tuition and scholarship remain unavailable/pending.')
    print('No scholarship rules or deposit policies changed; numeric deposits and EU tuition preserved.')
    print('Report:',output);print('Applied accepted replacements and source/audit records.' if apply else 'Preview only. No database records changed.')
    return output

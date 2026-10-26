"""Review all 21 cases; apply only the confirmed duplicate and online-route corrections."""
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from bs4 import BeautifulSoup
from app.models import Course, Tuition, TuitionSource, TuitionUpdateAudit, ScholarshipRule
from app.scraping.uk.arden.study_calculation import eligibility_questions, LEVELS_IMPORTER

ROOT=Path(settings.BASE_DIR)
OWNER='arden_pending_21_correction_v4'
TARGETS={'C_UK004_26','C_UK004_47'}
CANONICAL='C_ARD004_01'
DEFAULT='logs/arden/pending-21-investigation-20261023T095806836678Z/pending-21-evidence.json'


def normalized(text):
    return re.sub(r'[^a-z0-9]','',text.lower())


def state(source):
    if source is None:return None
    return {key:getattr(source,key) for key in ['academic_year','source_url','source_course_code','source_course_name','fee_basis','raw_fees']}


def canonical(value):
    return json.loads(json.dumps(value,cls=DjangoJSONEncoder))


def verified_capture(folder,item):
    data=(folder/Path(item['file']).name).read_bytes()
    if hashlib.sha256(data).hexdigest()!=item['sha256']:
        raise ValueError('Evidence hash mismatch: '+item['file'])
    return data


def run(*,apply=False,accept_duplicate_retirement=False,accept_route_correction=False,evidence_path=DEFAULT):
    if apply and not (accept_duplicate_retirement and accept_route_correction):
        raise ValueError('Apply requires accept_duplicate_retirement=True and accept_route_correction=True after review.')
    path=Path(evidence_path)
    if not path.is_absolute():path=ROOT/path
    evidence=json.loads(path.read_text(encoding='utf-8-sig'));folder=path.parent
    if evidence.get('university_id')!='UK004' or len(evidence.get('courses',[]))!=21:
        raise ValueError('Expected the collected 21-course Arden investigation.')
    for item in evidence['pages']+evidence['fees']:verified_capture(folder,item)
    reviewed=json.loads((ROOT/'data/universities/uk/arden/pending_21_findings.json').read_text(encoding='utf-8'))
    originals={c['course_id']:c for c in evidence['courses']}
    source_urls={
        'C_UK004_26':'https://arden.ac.uk/our-courses/undergraduate/business-degree/ba-hons-business-management-top-up',
        'C_UK004_47':'https://arden.ac.uk/our-courses/undergraduate/psychology-degrees/ba-hons-hrm-psychology1',
    }
    source_info={}
    for cid,url in source_urls.items():
        matches=[p for p in evidence['pages'] if p['requested_url']==url]
        if len(matches)!=1 or matches[0]['http_status']!=200 or matches[0]['redirects'] or matches[0]['source_url']!=url:
            raise ValueError(cid+': direct successful source required.')
        page=matches[0];soup=BeautifulSoup(verified_capture(folder,page),'html.parser')
        title=soup.find('input',attrs={'name':'apply_now_course'}).get('value','').replace('\u200b','').strip()
        course_id=soup.find('input',attrs={'name':'fees_course_id'}).get('value')
        if normalized(title)!=normalized(originals[cid]['title']):raise ValueError(cid+': source title differs.')
        captures=[r for r in evidence['fees'] if r['course_page']==url]
        if len(captures)!=2 or {r['country'] for r in captures}!={'Nigeria','Ghana'}:
            raise ValueError(cid+': both country fee responses required.')
        texts=[]
        for capture in captures:
            data=verified_capture(folder,capture)
            if capture['request_parameters']['course_id']!=course_id:raise ValueError(cid+': response course ID differs.')
            text=BeautifulSoup(json.loads(data)['data'],'html.parser').get_text(' ',strip=True)
            if text!=capture['text']:raise ValueError(cid+': captured fee text differs.')
            texts.append(text)
        if texts[0]!=texts[1]:raise ValueError(cid+': country fee quotes differ.')
        source_info[cid]=(page,title,course_id,captures,texts[0],soup)
    hrm_page,hrm_title,hrm_code,hrm_fees,hrm_text,hrm_soup=source_info['C_UK004_47']
    if re.findall(r'Level ([456]) fee: £([\d,]+)',hrm_text)!=[('4','5,500'),('5','5,500'),('6','5,500')]:
        raise ValueError('HRM published schedule changed; review before importing.')
    selector=hrm_soup.find('select',attrs={'name':'apply_now_course_study_type'})
    options=[o.get('value') for o in selector.find_all('option') if o.get('value')] if selector else []
    if options:raise ValueError('HRM now has application options; review them instead of saving intake-pending metadata.')
    if 'Part-time: 3 years+ (Online)' not in hrm_page['text']:
        raise ValueError('HRM online duration not confirmed.')
    report=dict(generated_at=datetime.now(timezone.utc).isoformat(),mode='apply' if apply else 'preview',database_changes=False,
                evidence_path=str(path),evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                all_21_findings=reviewed['findings'],changes=[],scholarship_rules_changed=False,deposit_policies_changed=False,
                note='No new scholarship deduction is verified by this evidence. Canonical top-up rules continue calculating eligible deductions. HRM intake and award scope remain pending.')
    with transaction.atomic():
        cq=Course.objects.filter(university_id='UK004',course_id__in=TARGETS|{CANONICAL})
        tq=Tuition.objects.filter(course__university_id='UK004',course_id__in=TARGETS|{CANONICAL},residency='INTL')
        sq=TuitionSource.objects.filter(tuition__course__university_id='UK004',tuition__course_id__in=TARGETS|{CANONICAL},tuition__residency='INTL')
        if apply:cq=cq.select_for_update();tq=tq.select_for_update();sq=sq.select_for_update()
        courses={c.course_id:c for c in cq};tuitions=list(tq);sources={s.tuition_id:s for s in sq}
        def tuition_for(cid):
            rows=[t for t in tuitions if t.course_id==cid]
            if len(rows)!=1 or rows[0].currency!='GBP':raise ValueError(cid+': one GBP international tuition required.')
            return rows[0]
        canonical_course=courses.get(CANONICAL);canonical_tuition=tuition_for(CANONICAL);canonical_source=sources.get(canonical_tuition.pk)
        top_page,top_title,top_code,top_fees,top_text,_=source_info['C_UK004_26']
        if (not canonical_course or not canonical_course.is_active or not canonical_source
                or canonical_source.raw_fees.get('verified') is not True
                or canonical_source.raw_fees.get('importer')!='arden_campus_topup_v2'
                or canonical_source.source_course_code!=top_code
                or canonical_course.course_url!=top_page['source_url']
                or normalized(canonical_course.title)!=normalized(top_title)
                or canonical_source.fee_basis!='annual_tuition'
                or Decimal(str(canonical_tuition.tuition_fee))!=Decimal('16000')
                or canonical_source.raw_fees.get('annual_tuition')!='16000.00'):
            raise ValueError('The existing canonical top-up does not match the verified source; no retirement performed.')
        for expected in ['Manchester Campus Fee per year: £16,000','London, Stratford Campus Fee per year: £16,000']:
            if expected not in top_text:raise ValueError('Canonical fee no longer matches the fresh quote.')
        # Retiring a duplicate must not silently remove a separately verified award scope.
        for rule in ScholarshipRule.objects.filter(scholarship__university_id='UK004',verified=True):
            scope=rule.rules.get('conditions',{}).get('course_id',[])
            if 'C_UK004_26' in scope:raise ValueError('Duplicate has a verified scholarship scope; reconcile it before retirement.')
        plans=[]
        for cid in sorted(TARGETS):
            course=courses.get(cid);tuition=tuition_for(cid);old_source=sources.get(tuition.pk)
            if course is None:raise ValueError(cid+': course missing.')
            if old_source and old_source.raw_fees.get('importer')!=OWNER:raise ValueError(cid+': existing source requires separate review.')
            if not old_source:
                for field in ['title','level','duration','location','start_month','start_year','intake_months','course_url','is_active']:
                    if getattr(course,field)!=originals[cid][field]:raise ValueError(cid+': database changed after investigation: '+field)
            page,title,code,captures,text,_=source_info[cid]
            common=dict(importer=OWNER,verified=False,currency='GBP',source_fee_category='International',course_source_url=page['source_url'],
                source_html_sha256=page['sha256'],fee_response_sha256=[r['sha256'] for r in captures],
                evidence_report_sha256=report['evidence_sha256'],mapping_reviewed=True)
            if cid=='C_UK004_26':
                after_course={'is_active':False}
                raw={**common,'academic_year':canonical_source.academic_year,'record_status':'duplicate_retired',
                     'canonical_course_id':CANONICAL,'course_kind':'undergraduate_topup_duplicate',
                     'verification_reason':'This duplicate record has been retired. Use the reviewed Business Management top-up '+CANONICAL+'.',
                     'scholarship_review':{'reason':'Verified deductions continue on the canonical course. No additional award is created on a duplicate.','eligibility_questions':[]},
                     'deposit_review':{'reason':'Use the canonical course deposit policy; the individual amount is advised by Arden.','eligibility_questions':[]}}
                year=canonical_source.academic_year;basis='unavailable'
            else:
                after_course={'location':'Online','duration':'3 years+ (part-time)','course_url':page['source_url'],'start_month':'','intake_months':''}
                if Course._meta.get_field('start_year').null:after_course['start_year']=None
                raw={**common,'academic_year':'','course_kind':'undergraduate_taught_online','study_mode':'online',
                     'fee_schedule_verified':True,'fees_by_level':{'4':'5500.00','5':'5500.00','6':'5500.00'},
                     'intake_verification_status':'pending_no_application_options','academic_year_basis':'No fee academic year is assigned without a verified intake.',
                     'verification_reason':'The online route and £5,500 per-level schedule are confirmed, but no current application intake is published in the capture. Applicable intake/year tuition remains unavailable.',
                     'scholarship_status':'programme_scope_and_intake_unconfirmed',
                     'scholarship_review':{'reason':'Do not apply Business Management or Psychology awards merely because both subjects appear in this joint degree. An applicable published scope and intake are required.','eligibility_questions':eligibility_questions(raw={'importer':LEVELS_IMPORTER})},
                     'deposit_review':{'reason':'Campus pre-CAS deposit policy does not apply to this online route. Online payment requirements require separate confirmation.','eligibility_questions':[]},
                     'fee_note':'Published schedule £5,500 per level; intake applicability unverified. Historical stored tuition is retained and must not be displayed as a verified current quote.'}
                year='';basis='per_level'
            for field,value in after_course.items():
                maximum=Course._meta.get_field(field).max_length
                if maximum and value is not None and len(str(value))>maximum:raise ValueError(cid+': '+field+' exceeds model length.')
            defaults=dict(academic_year=year,source_url=captures[0]['source_url'],source_course_code=code,source_course_name=page['heading'],fee_basis=basis,raw_fees=raw)
            money=dict(tuition_fee=str(tuition.tuition_fee),scholarship=str(tuition.scholarship or 0),scholarship_source_id=tuition.scholarship_source_id,tuition_deposit=str(tuition.tuition_deposit) if tuition.tuition_deposit is not None else None)
            before=canonical(dict(course={k:getattr(course,k) for k in after_course},tuition=money,source=state(old_source)))
            after=canonical(dict(course=after_course,tuition=money,source=defaults));changed=before!=after
            plans.append((course,tuition,after_course,defaults,before,after,changed))
            report['changes'].append(dict(course_id=cid,changed=changed,before=before,after=after))
            print(cid+': '+('retire duplicate; canonical '+CANONICAL if cid.endswith('_26') else 'confirm online route; fee intake and scholarship remain pending')+'; '+('update' if changed else 'unchanged'))
        writes=0
        if apply:
            for course,tuition,changes,defaults,before,after,changed in plans:
                if not changed:continue
                for field,value in changes.items():setattr(course,field,value)
                course.save(update_fields=[*changes,'updated_at'])
                TuitionSource.objects.update_or_create(tuition=tuition,defaults=defaults)
                TuitionUpdateAudit.objects.create(tuition=tuition,course_identifier=course.course_id,residency='INTL',before=before,after=after)
                writes+=1
        report.update(changed_records=sum(p[-1] for p in plans),applied_records=writes,database_changes=bool(writes),status='transaction_prepared' if apply else 'preview')
        output=ROOT/'logs/arden'/('pending-21-corrections-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    report['status']='applied' if apply else 'preview';output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('Changed/proposed records:',report['changed_records'])
    print('After these corrections: one duplicate retired; 20 active tuition cases remain (7 unpublished, 1 confirmed route with unverified intake, 12 other route/ownership cases).')
    print('No new scholarship deduction verified. Existing canonical top-up deductions remain active.')
    print('Numeric tuition, stored discounts, deposits and EU tuition unchanged.')
    print('Report:',output);print('Applied supported corrections.' if apply else 'Preview only. No database records changed.')
    return output

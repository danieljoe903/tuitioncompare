"""Build a deterministic evidence-only fee preview for 30 reviewed exact routes."""
import re
from decimal import Decimal

def plan(data, manifest):
    pages={p['source_url']:p for p in data['pages']}
    courses={r['database_course']['course_id']:r['database_course'] for r in data['courses']}
    tuition=data['database_snapshot']['tuitions']
    proposals=[];blocked=[]
    for m in manifest:
        cid=m['course_id']
        try:
            c=courses[cid];p=pages[m['source_url']]
            if p['http_status']!=200 or p['sha256']!=m['source_sha256'] or p['heading']!=m['heading']:
                raise ValueError('Source identity/hash differs from reviewed capture.')
            if c['level']!=m['level'] or c['university_id']!='UK007' or not c['is_active']:
                raise ValueError('Database owner/level/activity differs from reviewed route.')
            text=' '.join(p['text'].split())
            fees=re.findall(r'International students \((202[67]/2[78])\) (First year tuition fee|Annual tuition fees|Tuition fees): £([\d,]+)',text)
            expected=(m['academic_year'],m['fee_label'],str(int(Decimal(m['tuition_fee']))))
            if len(fees)!=1 or (fees[0][0],fees[0][1],fees[0][2].replace(',',''))!=expected:
                raise ValueError('Cannot isolate the reviewed international fee/cohort.')
            duration=re.search(r'\bDuration (.+?) (?:UCAS code\(s\)|Start date)',text)
            if not duration or duration.group(1)!=m['published_duration']:
                raise ValueError('Published duration differs from reviewed capture.')
            if 'Location: Aston University, Birmingham' not in text:
                raise ValueError('Exact Birmingham campus evidence missing.')
            if m['start_month']+' 2027' not in text:
                raise ValueError('Reviewed intake not found.')
            if m['fee_label']=='Tuition fees' and 'payment of an annual tuition fee' not in text:
                raise ValueError('Annual basis not established for the generic tuition label.')
            ts=[t for t in tuition if t['course_id']==cid and t['residency']=='INTL']
            if len(ts)!=1 or ts[0]['currency']!='GBP':
                raise ValueError('Expected one GBP international tuition row.')
            after=dict(course_url=m['source_url'],start_month=m['start_month'],start_year=2027,
                duration=m['published_duration'])
            code=re.search(r'UCAS code\(s\) ([A-Z0-9]{4})\b',text)
            if code:after['ucas_cao_code']=code.group(1)
            changes={k:dict(before=c.get(k),after=v) for k,v in after.items() if c.get(k)!=v}
            proposals.append(dict(**m,currency='GBP',before_course=c,before_international_tuition=ts[0],
                proposed_course=after,course_changes=changes,fee_note=(
                    'Published first-year international tuition; placement-year fees are separate and later-year fees may change.'
                    if m['fee_basis']=='first_year_tuition' else
                    'Published annual international tuition for the selected intake. This is not a guaranteed total programme price.'),
                mapping='reviewed_exact_award_and_subject',scholarship_status='separate_cohort_scope_reconciliation_required',
                deposit_status='offer_and_policy_scope_reconciliation_required'))
        except (KeyError,ValueError,TypeError) as exc:blocked.append(dict(course_id=cid,reason=str(exc)))
    return dict(university_id='UK007',database_changes=False,mappings_applied=False,proposals=proposals,blocked=blocked,
        summary=dict(fee_proposals=len(proposals),blocked=len(blocked),
            undergraduate_first_year_fees=sum(p['fee_basis']=='first_year_tuition' for p in proposals),
            postgraduate_annual_fees=sum(p['fee_basis']=='annual_tuition' for p in proposals)))

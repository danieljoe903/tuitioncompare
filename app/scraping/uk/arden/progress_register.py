"""Merge saved university audits into one cumulative register; retain history."""
import json
from datetime import datetime, timezone
from pathlib import Path

OWNER='university_scraping_progress_v1'


def audit_date(report,path=None):
    stamp=report.get('generated_at') or report.get('updated_at')
    try:return datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError):return path.stat().st_mtime if path else 0


def discover(root,previous,current,current_path):
    seed_path=root/'data/universities/progress/combined_audit_reference.json'
    if not seed_path.is_file():raise ValueError('Missing original combined audit reference: '+str(seed_path))
    seeds=json.loads(seed_path.read_text(encoding='utf-8'))['universities']
    records=dict(previous.get('universities',{}))
    for uid,entry in seeds.items():
        if uid not in records or entry.get('audit_timestamp',0)>records[uid].get('audit_timestamp',0):
            records[uid]=entry
    # Use only saved audit reports with an explicit university ID and summary.
    # Raw exports, source reviews and matching previews are not coverage audits.
    for path in sorted((root/'logs/scraping-progress').rglob('*.json')):
        if not any(word in path.name.lower() for word in ['audit','handoff']):continue
        if path.name in {'university_scraping_progress_register.json','arden_latest_handoff.json'}:continue
        try:report=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError):continue
        uid=report.get('university_id')
        summary=report.get('summary')
        if not uid or not isinstance(summary,dict) or 'verified_international_fees' not in summary:continue
        candidate=dict(audit_path=str(path),audit_timestamp=audit_date(report,path),report=report)
        existing=records.get(uid)
        if not existing or candidate['audit_timestamp']>existing.get('audit_timestamp',0):records[uid]=candidate
    # The audit just executed is authoritative for Arden, even if an older log
    # was copied later and therefore has a misleading filesystem timestamp.
    records[current['university_id']]=dict(audit_path=str(current_path),audit_timestamp=audit_date(current),report=current)
    return records


def cell(value):
    if value is None:return 'Not audited'
    return str(value).replace('|','/').replace('\n',' ')


def arden_section(report):
    lines=['<!-- university-audit:UK004:start -->',
           '## UK — Arden University (UK004): latest saved-metadata audit','',
           'Generated: '+report['generated_at'],'',
           'Status: reviewed with outstanding cases. This command queried Arden only; earlier university entries retain their original provenance and caveats.','',
           '| Measure | Audited count |','|---|---:|']
    for key,value in report['summary'].items():
        if not isinstance(value,dict):lines.append('| '+key.replace('_',' ')+' | '+str(value)+' |')
    lines+=['','Fee bases: '+json.dumps(report['summary'].get('verified_fee_bases',{})),
            '', 'Fee years: '+json.dumps(report['summary'].get('verified_fee_years',{})),'',
            '### Outstanding tuition/mapping cases','','| Course | Cause / next action |','|---|---|']
    for row in report['pending_fee_cases']:
        reason=str(row.get('fee_review_reason') or '')+' '+str(row.get('next_action') or '')
        lines.append('| '+cell(row['course_id']+' — '+row['title'])+' | '+cell(reason)+' |')
    lines+=['','### Verified-fee courses with scholarship applicability pending','','| Course | Saved review |','|---|---|']
    for row in report['verified_fee_scholarship_scope_gaps']:
        review=row.get('scholarship_review') or {}
        lines.append('| '+cell(row['course_id']+' — '+row['title'])+' | '+cell(review.get('reason') if isinstance(review,dict) else review)+' |')
    lines+=['','### Calculation rules, questions and deposit treatment','',
            'Verified awards calculate net tuition when required facts are confirmed. Alumni and Global Tiers do not stack; the greatest eligible offer wins. Missing required answers block that award. Full rule conditions and eligibility questions remain in the JSON audit/database snapshot.','',
            'Online Alumni facts: arden_alumni_qualifying_degree, arden_alumni_within_five_years, arden_alumni_official_results_received, arden_alumni_advisor_informed, arden_alumni_offer_granted. Common offer, partner, account/status and exclusion checks remain required. Global Tiers country/intake/price guards are preserved.','',
            'The verified campus deposit policy advises the individual amount during application; no universal numeric amount is verified. The campus policy does not apply to the reviewed online routes; online payment requirements remain separately unverified.','',
            'Manchester top-up early-bird combination remains pending the published arithmetic discrepancy. The LLM has a conditional Alumni deduction; its separate Global Tiers price group remains unconfirmed.','',
            '### Audit limitations','']
    lines+=['- '+n for n in report['notes']]
    lines+=['','Expected-count differences: '+json.dumps(report.get('expected_count_differences',{})),'']
    lines+=['- '+n for n in report.get('anomalies',[])]
    lines+=['','<!-- university-audit:UK004:end -->','']
    return '\n'.join(lines)


def preserve_combined(text,report):
    start='<!-- university-audit:UK004:start -->'
    end='<!-- university-audit:UK004:end -->'
    block=arden_section(report)
    if start in text or end in text:
        if text.count(start)!=1 or text.count(end)!=1 or text.index(end)<text.index(start):
            raise ValueError('Arden register markers are inconsistent; preserve original and review.')
        a=text.index(start);b=text.index(end)+len(end)
        return text[:a]+block.rstrip('\n')+text[b:]
    return text.rstrip()+'\n\n'+block


def update(root,current,current_path,folder,write_json):
    root=Path(root)
    base=root/'logs/scraping-progress'
    json_path=base/'university_scraping_progress_register.json'
    md_path=base/'university_scraping_progress_register.md'
    previous={}
    if json_path.exists():
        previous=json.loads(json_path.read_text(encoding='utf-8'))
        if previous.get('owner')!=OWNER or not isinstance(previous.get('universities'),dict):
            raise ValueError('Existing cumulative JSON has an unknown structure; preserve and review it: '+str(json_path))
    records=discover(root,previous,current,current_path)
    archive=None
    if md_path.exists():
        archive=folder/'previous-progress-register.md'
        archive.write_bytes(md_path.read_bytes())
    register={**previous,'owner':OWNER,'updated_at':current['generated_at'],
              'universities':records,'uk_inventory':current['uk_inventory'],
              'previous_markdown_archive':str(archive) if archive else previous.get('previous_markdown_archive'),
              'missing_prior_audits':[uid for uid in ['UK001','UK002','UK003'] if uid not in records],
              'database_changes':False}
    # Each snapshot contains the merged records so a later audit is reversible.
    write_json(folder/'cumulative-progress-register.json',register)
    write_json(json_path,register)
    reference_md=root/'data/universities/progress/university_scraping_progress_register.md'
    if not reference_md.is_file():raise ValueError('Missing original combined register: '+str(reference_md))
    baseline=reference_md.read_text(encoding='utf-8')
    if not all(name in baseline for name in ['Abertay University (UK001)','Aberystwyth University (UK002)','Anglia Ruskin University (UK003)']):
        raise ValueError('Combined reference is missing prior university entries.')
    # Recover the original combined narrative if the earlier dashboard-only
    # version omitted Abertay/Aberystwyth. Preserve any richer existing copy.
    if md_path.exists():
        old_text=md_path.read_text(encoding='utf-8')
        if all(name in old_text for name in ['Abertay University (UK001)','Aberystwyth University (UK002)','Anglia Ruskin University (UK003)']):baseline=old_text
    content=preserve_combined(baseline,current)
    temporary=md_path.with_name(md_path.name+'.tmp')
    temporary.write_text(content,encoding='utf-8')
    temporary.replace(md_path)
    (folder/'uk-progress-register.md').write_text(content,encoding='utf-8')
    return json_path,md_path,register['missing_prior_audits']

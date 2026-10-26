"""Replace only Aston's section; preserve six previous university narratives."""
START='<!-- university-audit:UK007:final:start -->'
END='<!-- university-audit:UK007:final:end -->'
PRIOR=['UK001','UK002','UK003','UK004','UK005','UK006']
def merge_register(text,report):
    for uid in PRIOR:
        if uid not in text:raise ValueError('Previous university entry missing: '+uid)
    if START in text or END in text:
        if text.count(START)!=1 or text.count(END)!=1 or text.index(START)>text.index(END):
            raise ValueError('Malformed Aston register markers; no overwrite.')
    text=text.replace('| UK | 146, confirmed by user database export | 6 | 140 | Abertay, Aberystwyth, ARU, Arden, AUB and AUP reviewed with outstanding cases retained |', '| UK | 146, confirmed by user database export | 7 | 139 | Abertay, Aberystwyth, ARU, Arden, AUB, AUP and Aston reviewed with outstanding cases retained |')
    text=text.replace('The 140 UK universities are a pending queue','The 139 UK universities are a pending queue')
    lines=[START,'## UK — Aston University (UK007): final saved-data audit','',
        'Generated: '+report['generated_at'],'','Status: reviewed with outstanding cases.','',
        '| Measure | Audited value |','|---|---:|']
    lines += ['| '+k.replace('_',' ')+' | '+str(v)+' |' for k,v in report['summary'].items()]
    lines += ['', '### Unconfirmed routes — reasons retained', '']
    for cid,row in report['followups']['routes'].items():
        lines += ['- '+cid+' — '+row['title']+': '+row['message']+' Follow-up: '+row['confirmation_needed']]
    lines += ['', '### Scholarship and deposit follow-up','',
        '- MBA January 2027 and September 2027: '+report['followups']['mba_scholarships']['message'],
        '- Top-up has a conditional GBP 3,000 single Community payment. Exact Top-up entry/non-partner confirmation required; Cadbury applicability was not inferred.',
        '- MPH has conditional September Community GBP 5,500; MPH is excluded from Cadbury.',
        '- Advanced Computer Science has conditional January Community GBP 4,000 or confirmed Cadbury GBP 10,000; never stacked.',
        '- Medicine, Optometry, Pharmacy, Audiology and both MBA course records are excluded from the reviewed Community/Cadbury schemes. This does not prove that no other scholarship exists.',
        '- Deposit amounts remain offer-letter specific. Both MBA course pages publish GBP 5,000 at acceptance, included in tuition; exact offer and waiver confirmation are required for a numeric calculation.',
        '- Comparison has no scholarship questions; blank residence retains results. Confirmation questions belong on the scholarship page.',
        '- User-reported validation: 960 synthetic award/deposit API scenarios passed across 48 fee routes; this is not confirmation of any real award or payment.',
        '- Refresh scheduling is still separate work; this audit does not install an automatic refresh.',
        '', 'Audit file: `'+report['audit_path']+'`','',
        'Next UK university: '+str(report.get('next_university_id') or 'none found')+'.',END]
    section='\n'.join(lines)
    if START in text:
        a=text.index(START);b=text.index(END)+len(END)
        return text[:a]+section+text[b:]
    return text.rstrip()+'\n\n'+section+'\n'

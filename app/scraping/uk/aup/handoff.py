"""Preserve previous university narratives and replace only AUP's audit section."""
import json
START='<!-- university-audit:UK006:final:start -->'
END='<!-- university-audit:UK006:final:end -->'

def merge_register(text, report):
    for uid, name in [('UK001','Abertay'),('UK002','Aberystwyth'),('UK003','Anglia Ruskin'),('UK004','Arden'),('UK005','Arts University Bournemouth')]:
        if uid not in text or name not in text:
            raise ValueError('Existing combined register is missing '+uid+'; no overwrite.')
    text=text.replace('| UK | 146, confirmed by user database export | 5 | 141 | Abertay, Aberystwyth, ARU, Arden and AUB reviewed with outstanding cases retained |',
        '| UK | 146, confirmed by user database export | 6 | 140 | Abertay, Aberystwyth, ARU, Arden, AUB and AUP reviewed with outstanding cases retained |')
    text=text.replace('The 141 UK universities are a pending queue','The 140 UK universities are a pending queue')
    lines=[START,'## UK — Arts University Plymouth (UK006): final saved-data audit','',
           'Generated: '+report['generated_at'],'',
           'Status: reviewed with outstanding cases. Applicant awards are conditional.',
           'Six UK universities have now had a review pass; this is not a claim that all outstanding cases are resolved.','',
           '| Measure | Audited value |','|---|---:|']
    for key,value in report['summary'].items():
        lines.append('| '+key.replace('_',' ')+' | '+str(value)+' |')
    lines += ['', '### Outstanding follow-up', '',
        '- Graphic Design Top-up (C_AUP006_01): exact current route/intake and applicable fee require confirmation.',
        '- MA Drawing (C_UK006_18): separate current recruiting route/intake and applicable fee require confirmation.',
        '- Three published competitive award cycles: Has ended (deadline 25 February 2026). September 2027 availability remains unconfirmed. Recheck official terms when updated; do not infer permanent cancellation or carry the old deadline forward.',
        '- MA Painting (C_UK006_24) is historical/inactive. Its documented transition links to the existing MA Fine Art (C_UK006_17), avoiding duplicate current results.',
        '- Seven reviewed MAs have a conditional 15% Alumni deduction: GBP 18,000 less GBP 2,700 = GBP 15,300 only with saved university confirmation for the applicant/intake. No individual award is inferred from rule coverage.',
        '- Deposit policy covers 23 verified routes: GBP 2,000 before CAS where a Student visa is required, included in tuition; remaining balance due in full at enrolment. Unknown visa status shows guidance, not a confirmed amount or zero.',
        '- The UCA-linked legacy award remains blocked because it belongs to another university; it is not reclassified as an expired AUP award.',
        '- Comparison form has no scholarship questions. Preserve results with blank residence. Eligibility and award confirmation belong on the scholarship page; comparison notes direct applicants there.',
        '- User supplied result: 212 synthetic comparison API scenarios passed. These are test scenarios, not applicant grants or payments.',
        '', 'Audit file: `'+report['audit_path']+'`', '',
        'Next UK university: '+str(report.get('next_university_id') or 'none found')+'.', END]
    section='\n'.join(lines)
    if START in text or END in text:
        if text.count(START)!=1 or text.count(END)!=1 or text.index(START)>text.index(END):
            raise ValueError('AUP register markers are malformed; no overwrite.')
        a=text.index(START);b=text.index(END)+len(END)
        return text[:a]+section+text[b:]
    return text.rstrip()+'\n\n'+section+'\n'

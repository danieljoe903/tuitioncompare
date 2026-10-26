"""Offline URL and candidate guards; does not fetch pages or import Django."""
import ast
from pathlib import Path
import re
from difflib import SequenceMatcher
from urllib.parse import urljoin,urlsplit,urlunsplit
ROOT=Path(__file__).resolve().parents[4]
p=ROOT/'app/scraping/uk/aup/commands/inspect_aup_sources.py'
module=ast.parse(p.read_text(encoding='utf-8'))
env=dict(re=re,SequenceMatcher=SequenceMatcher,urljoin=urljoin,urlsplit=urlsplit,urlunsplit=urlunsplit)
for node in module.body:
    if isinstance(node,ast.FunctionDef) and node.name in {'official','clean_url','title_key','course_link','policy_link','candidates'}:
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(p),'exec'),env)
assert env['official']('https://www.aup.ac.uk/courses')
assert env['official']('https://clearing.aup.ac.uk/international/finance')
for url in ['https://www.uca.ac.uk/study-at-uca/scholarships/','https://aup.ac.uk.example.com/courses','http://www.aup.ac.uk/courses','https://user@www.aup.ac.uk/courses']:
    assert not env['official'](url),url
assert env['title_key']('BA (Hons) Game Arts & Animation')==env['title_key']('BA (Hons) Game Arts and Animation')
assert env['title_key']('CertHE Ceramics & Glass Launching Sept 2026')==env['title_key']('CertHE Ceramics & Glass')
link=dict(text='BA (Hons) Graphic Design',url='https://www.aup.ac.uk/courses/undergraduate/ba-hons-graphic-design')
assert env['course_link'](link)
rows=env['candidates']({'title':'BA (Hons) Graphic Design','level':'UG'},[link])
assert len(rows)==1 and rows[0]['status']=='exact_title_candidate' and rows[0]['mapping_approved'] is False
rows=env['candidates']({'title':'BA (Hons) Graphic Design (Top-up)','level':'TOPUP'},[link])
assert rows and rows[0]['status']=='advanced_entry_requires_review'
assert not env['candidates']({'title':'MA Graphic Design','level':'PG'},[link])
assert env['policy_link']({'text':'Fees & Funding','url':'https://www.aup.ac.uk/study/postgraduate/fees-finance-funding'})
assert env['clean_url']('https://www.aup.ac.uk/courses/#section')=='https://www.aup.ac.uk/courses'
print('PASS: official-host, wrong-university, title-normalisation, candidate-only, advanced-entry and award-level guards.')
print('Offline helpers only; no source collection or database changes.')

"""Offline URL/candidate guards. No Django or database writes."""
import ast,re
from pathlib import Path
from difflib import SequenceMatcher
from urllib.parse import urlsplit,urlunsplit,urljoin
root=Path(__file__).resolve().parents[4]
tree=ast.parse((root/'app/scraping/uk/aston/commands/inspect_aston_sources.py').read_text())
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name!='fetch']
env=dict(re=re,urlsplit=urlsplit,urlunsplit=urlunsplit,urljoin=urljoin,SequenceMatcher=SequenceMatcher)
exec(compile(ast.Module(body=functions,type_ignores=[]),'helpers','exec'),env)
assert env['official']('https://www.aston.ac.uk/courses')
assert not env['official']('https://aston.ac.uk.evil.example/courses')
assert not env['official']('https://attacker@www.aston.ac.uk/courses')
assert env['course_link']({'url':'https://www.aston.ac.uk/study/courses/computer-science-bsc','text':'Computer Science BSc'})
assert env['catalogue_link']({'url':'https://www.aston.ac.uk/courses?page=2'})
catalogue=[{'text':'Computer Science BSc','url':'https://www.aston.ac.uk/study/courses/computer-science-bsc'},
           {'text':'Computer Science MSc','url':'https://www.aston.ac.uk/study/courses/computer-science-msc'}]
rows=env['candidates']({'title':'BSc Computer Science','level':'UG'},catalogue)
assert all('msc' not in r['source_url'] for r in rows)
print('PASS: official-host, credential, suffix-award, pagination and level guards. No database records changed.')

from pathlib import Path
import runpy
n=runpy.run_path(str(Path(__file__).resolve().parents[4]/'app/scraping/uk/aston/reindex.py'))
assert n['subject']('MSc Business Analytics')==n['subject']('Business Analytics MSc')
assert not n['route']('https://www.aston.ac.uk.evil.example/study/courses/test')
rows=[dict(source_url='https://www.aston.ac.uk/study/courses/business-analytics-msc',labels={'Business Analytics MSc'},captured=True)]
assert n['candidates']({'title':'MSc Business Analytics','level':'PG'},rows)
assert not n['candidates']({'title':'BSc Business Analytics','level':'UG'},rows)
assert not n['candidates']({'title':'MRes Business Analytics','level':'MRES'},rows)
print('PASS: award-position matching, host and award-level guards. No database records changed.')

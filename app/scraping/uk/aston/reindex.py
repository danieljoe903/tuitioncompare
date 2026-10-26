"""Candidate discovery only: normalise award position, retain route differences."""
import re
from difflib import SequenceMatcher
from urllib.parse import urlsplit, unquote

AWARD=r'\b(?:bsc|ba|beng|meng|mpharm|moptom|mbchb|llb|msc|ma|mba|mph|llm|mres|mphil|phd|dba)\b'
UG={'bsc','ba','beng','meng','mpharm','moptom','mbchb','llb'}
PG={'msc','ma','mba','mph','llm'}
RES={'mres','mphil','phd','dba'}

def words(text):
    return re.sub(r'[^a-z0-9]+',' ',text.casefold().replace('&',' and ')).strip()

def subject(text):
    text=words(text)
    text=re.sub(AWARD,' ',text)
    text=re.sub(r'\b(?:hons|honours|full time|part time|master of public health|bachelor of science)\b',' ',text)
    return ' '.join(text.split())

def route(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in {'www.aston.ac.uk','aston.ac.uk'} or p.username or p.password:
        return False
    return p.path.startswith('/study/courses/')

def index(data):
    # One candidate URL, keeping all observed labels, headings and redirects.
    found={}
    def add(url,label,captured=False):
        if not route(url):return
        row=found.setdefault(url,dict(source_url=url,labels=set(),captured=False))
        if label:row['labels'].add(label)
        row['captured'] |= captured
    for page in data['pages']:
        if page.get('http_status')!=200:continue
        add(page['source_url'],page.get('heading',''),True)
        for link in page.get('links',[]):add(link['url'],link.get('text',''))
    return list(found.values())

def candidates(course, catalogue):
    target=subject(course['title']);out=[]
    for row in catalogue:
        path=unquote(urlsplit(row['source_url']).path)
        awards=set(re.findall(AWARD,words(path)))
        for label in row['labels']:awards.update(re.findall(AWARD,words(label)))
        level=course['level']
        allowed=RES if level=='MRES' else PG if level=='PG' else UG
        if not awards.intersection(allowed):continue
        labels=[]
        for label in row['labels']:
            # Search cards may append type/description after the qualification.
            cut=re.search(AWARD,label.casefold())
            labels.append(label[:cut.end()] if cut and cut.start()>0 else label)
        slug=path.split('/study/courses/',1)[1].split('/')[0].replace('-',' ')
        labels.append(slug)
        scores=[]
        for label in labels:
            key=subject(label)
            scores.append(max(SequenceMatcher(None,target,key).ratio(),
                SequenceMatcher(None,' '.join(sorted(target.split())),' '.join(sorted(key.split()))).ratio()))
        score=max(scores,default=0)
        if score<0.65:continue
        out.append(dict(source_url=row['source_url'],source_labels=sorted(row['labels']),score=round(score,3),
            page_collected=row['captured'],mapping_approved=False,
            status='candidate_only_route_campus_award_intake_review_required'))
    return sorted(out,key=lambda r:(-r['score'],not r['page_collected'],r['source_url']))[:8]

def repair(data):
    catalogue=index(data)
    rows=[dict(database_course=r['database_course'],candidates=candidates(r['database_course'],catalogue),mapping_approved=False,review_required=True) for r in data['courses']]
    summary=dict(data['summary'])
    summary.update(catalogue_unique_candidate_urls=len(catalogue),courses_with_candidates=sum(bool(r['candidates']) for r in rows),
        courses_without_candidates=sum(not r['candidates'] for r in rows),
        candidate_pages_not_collected=len({c['source_url'] for r in rows for c in r['candidates'] if not c['page_collected']}))
    return rows,summary

"""Targeted raw evidence for unresolved routes; no mapping or DB changes."""
import hashlib,json,re
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.timezone import now
from app.models import Course,Tuition,TuitionSource
from app.scraping.uk.aston.commands.inspect_aston_sources import fetch,links_from

PENDING=['C_AST007_01','C_AST007_RES_01','C_AST007_RES_02','C_AST007_RES_03','C_AST007_RES_04',
         'C_UK007_20','C_UK007_26','C_UK007_36','C_UK007_37','C_UK007_47','C_UK007_50']
URLS=[
 'https://www.aston.ac.uk/courses-atoz',
 'https://www.aston.ac.uk/study/courses/global-business-and-management-top-bsc/september-2027',
 'https://www.aston.ac.uk/study/courses/business-and-management-mres',
 'https://www.aston.ac.uk/study/courses/public-health-mph/september-2027',
 'https://www.aston.ac.uk/study/courses/computer-science-msc',
 'https://www.aston.ac.uk/study/courses/advanced-computer-science-msc/january-2027',
 'https://www.aston.ac.uk/study/courses/business-and-politics-bsc/september-2027',
 'https://www.aston.ac.uk/study/courses/business-and-sociology-bsc/september-2027',
 'https://www.aston.ac.uk/study/courses/data-science-and-artificial-intelligence-msc',
 'https://www.aston.ac.uk/study/courses/mba-full-time-mba/january-2027',
 'https://www.aston.ac.uk/study/courses/mba-full-time-mba/september-2027',
 'https://www.aston.ac.uk/education/quality/a-z/general-regulations',
 'https://www.aston.ac.uk/research/eps/aipt/jobs-study',
 'https://www.aston.ac.uk/postgraduate-research/research-areas',
]

class Command(BaseCommand):
    help='Collect14 targeted pages for11 Aston pending records and MBA intake context. No DB writes or mappings approved.'
    def handle(self,*args,**options):
        root=Path(settings.BASE_DIR);folder=root/'logs/aston'/('pending11-investigation-'+now().strftime('%Y%m%dT%H%M%S%fZ'))
        folder.mkdir(parents=True);pages=[];issues=[]
        with requests.Session() as session:
            session.headers['User-Agent']='UniversityTuitionCompare targeted evidence review/1.0'
            for i,url in enumerate(URLS,1):
                self.stdout.write('Reading: '+url)
                try:
                    status,actual,headers,raw,redirects=fetch(session,url)
                    filename=f'page-{i:02d}.html';(folder/filename).write_bytes(raw)
                    soup=BeautifulSoup(raw,'html.parser');main=soup.find('main') or soup
                    heading=main.find('h1') or soup.find('h1');text=' '.join(main.get_text(' ',strip=True).split())
                    pages.append(dict(requested_url=url,source_url=actual,http_status=status,redirects=redirects,
                        file=filename,sha256=hashlib.sha256(raw).hexdigest(),heading=heading.get_text(' ',strip=True) if heading else '',
                        text=text,links=links_from(soup,actual),
                        tables=[[[cell.get_text(' ',strip=True) for cell in tr.find_all(['th','td'])] for tr in table.find_all('tr')] for table in main.find_all('table')],
                        application_links=[dict(text=a.get_text(' ',strip=True),url=a['href']) for a in soup.select('a[href]') if any(w in a['href'].casefold() for w in ['ucas','apply','application'])]))
                    if status!=200:issues.append(dict(source_url=url,status=status,reason='Capture error, not evidence of closure.'))
                except Exception as exc:
                    # Do not persist authentication redirect tokens in error output.
                    reason=str(exc)
                    if 'redirect stopped' in reason:reason=reason.split('redirect stopped')[0]+'redirect stopped; no authentication attempted.'
                    issues.append(dict(source_url=url,reason=type(exc).__name__+': '+reason))
        context_ids=PENDING+['C_UK007_24']
        courses=list(Course.objects.filter(university_id='UK007',course_id__in=context_ids).order_by('course_id').values())
        report=dict(university_id='UK007',generated_at=now().isoformat(),database_changes=False,mappings_approved=False,
            pending_course_ids=PENDING,context_course_ids=['C_UK007_24'],database_courses=courses,
            international_tuitions=list(Tuition.objects.filter(course__university_id='UK007',course_id__in=context_ids,residency='INTL').values()),
            sources=list(TuitionSource.objects.filter(tuition__course__university_id='UK007',tuition__course_id__in=context_ids).values()),
            pages=pages,issues=issues,
            notes=['Five programmes are explicitly excluded from reviewed Community/Cadbury schemes, not all scholarships.',
                'MRes exact route found; applications-opening-soon does not establish a recruiting intake or fee.',
                'Global Business and Management Top-up is an explicit replacement candidate, not the stored title.',
                'Public Health exact2027 route found; check full-time course-fee basis and intake evidence.',
                'MBA24 and50 originally represented September and January. Do not retire either as duplicate without preserving the intake distinction. Existing24 was imported on the January route; evaluate how to restore distinct current intake records.',
                'Research regulations establish award categories only, not exact project, intake or international price.',
                'No related degree is substituted, no login attempted, and no HTTP error proves closure.'])
        path=folder/'evidence.json';path.write_text(json.dumps(report,cls=DjangoJSONEncoder,indent=2,ensure_ascii=False),encoding='utf-8')
        self.stdout.write(json.dumps(dict(pending_records=len(PENDING),database_context_courses=len(courses),pages_collected=len(pages),collection_issues=len(issues)),indent=2))
        self.stdout.write('Report: '+str(path));self.stdout.write('Evidence only. No mappings approved or database records changed.')

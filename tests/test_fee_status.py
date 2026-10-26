"""Standalone regression tests: python -m unittest discover -s tests -p test_fee_status.py -v"""
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from decimal import Decimal
import json

from django.conf import settings
if not settings.configured:
    settings.configure(DEFAULT_CHARSET='utf-8', SECRET_KEY='test')
from django.test import RequestFactory

# Load the endpoint with isolated model adapters; never use the real database.
PACKAGE = '_fee_status_regression'
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'app')]
sys.modules[PACKAGE] = package
class Query(list):
    def select_related(self, *a): return self
    def prefetch_related(self, *a): return self
class Manager:
    courses = []
    def filter(self, **kw): return Query(self.courses)
class MissingSource(Exception): pass
models = ModuleType(PACKAGE + '.models')
models.Course = NS(objects=Manager())
models.TuitionSource = NS(DoesNotExist=MissingSource)
sys.modules[models.__name__] = models
fees = ModuleType(PACKAGE + '.fee_models')
fees.fee_metadata = lambda t: {'academic_year': t.source_metadata.academic_year,
    'fee_label': 'Annual tuition fee', 'fee_note': ''}
sys.modules[fees.__name__] = fees
awards = ModuleType(PACKAGE + '.scholarship_calculation')
awards.calls = []
def calculate(t, candidates, profile, academic_year, study_year):
    awards.calls.append(t)
    discount = min(t.tuition_fee, t.scholarship)
    return {'discount': str(discount), 'net': str(t.tuition_fee-discount),
        'label': 'Stored discount', 'applied': [], 'review': [], 'notes': [],
        'academic_year': academic_year, 'study_year': study_year}
awards.calculate_scholarships = calculate
sys.modules[awards.__name__] = awards
selection = importlib.import_module(PACKAGE + '.fee_selection')
endpoint = importlib.import_module(PACKAGE + '.fee_comparison')

def source():
    return NS(academic_year='2026/27', source_url='https://example.edu/fees', raw_fees={
        'fee_categories': [dict(source_fee_category=category, academic_year='2026/27',
             currency='GBP', annual_tuition=value, amount_requires_review=False,
             footnote_marker='', annotation='')
          for category,value in [('International','16950.00'),
             ('England/Wales/N Ireland/Rep of Ireland','9790.00'), ('Scotland','1820.00')]]})

def course(name='Abertay University'):
    c=NS(university_id='UK001', course_id='C_UK001_02', title='Biomedical Science',
         university=NS(name=name, slug='abertay-university', currency='GBP',
                       scholarships=NS(all=lambda:[])), level='UG', duration='4 Years',
         location='Dundee', start_month='September', start_year=2026, course_url='original')
    t=NS(course=c, residency='INTL', tuition_fee=Decimal('16950'), scholarship=Decimal('3000'),
         scholarship_source_id=None, currency='GBP', tuition_deposit=Decimal('2000'),
         source_metadata=source())
    c.tuitions=NS(all=lambda:[t])
    return c,t

class FeeSelectionTest(unittest.TestCase):
    def test_roi_amount(self):
        self.assertEqual(selection.select_published_fee(source(),'ruk_roi','2026/27')['amount'],Decimal('9790'))
    def test_unknown(self):
        self.assertIsNone(selection.select_published_fee(source(),'unknown','2026/27')['amount'])
    def test_duplicate_rejected(self):
        s=source();s.raw_fees['fee_categories'].append(s.raw_fees['fee_categories'][0])
        self.assertFalse(selection.select_published_fee(s,'international','2026/27')['available'])
    def test_nan_rejected(self):
        s=source();s.raw_fees['fee_categories'][0]['annual_tuition']='NaN'
        self.assertFalse(selection.select_published_fee(s,'international','2026/27')['available'])
    def test_wrong_year_rejected(self):
        self.assertFalse(selection.select_published_fee(source(),'international','2027/28')['available'])
    def test_fallback_missing_roi(self):
        s=source();s.raw_fees['fee_categories']=s.raw_fees['fee_categories'][:1]
        self.assertFalse(selection.select_published_fee(s,'ruk_roi','2026/27')['available'])
    def test_footnote_rejected(self):
        s=source();s.raw_fees['fee_categories'][0]['footnote_marker']='*'
        self.assertFalse(selection.select_published_fee(s,'international','2026/27')['available'])

class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.c,self.t=course();models.Course.objects.courses=[self.c];awards.calls=[]
    def request(self, status):
        r=RequestFactory().get('/api/compare-tuition/', {'destination':'UK', 'programme':'UG',
            'subject':'Biomedical', 'fee_status':status})
        response=endpoint.compare_tuition(r)
        return response,json.loads(response.content)
    def test_international_keeps_calculation(self):
        _,d=self.request('international');r=d['results'][0]
        self.assertEqual(r['net_tuition'],13950);self.assertEqual(len(awards.calls),1)
    def test_roi_does_not_use_international_discount_or_deposit(self):
        _,d=self.request('ruk_roi');r=d['results'][0]
        self.assertEqual(r['tuition_fee'],9790);self.assertEqual(r['net_tuition'],9790)
        self.assertEqual(r['scholarship'],0);self.assertIsNone(r['tuition_deposit'])
        self.assertEqual(awards.calls,[]);self.assertEqual(self.t.tuition_fee,Decimal('16950'))
        self.assertEqual(self.c.course_url,'original')
    def test_unknown_returns_nulls(self):
        _,d=self.request('unknown');r=d['results'][0]
        self.assertFalse(r['fee_available']);self.assertIsNone(r['net_tuition'])
    def test_other_school_not_given_abertay_rate(self):
        self.c.university.name='Another University'
        _,d=self.request('ruk_roi');self.assertFalse(d['results'][0]['fee_available'])
    def test_invalid_status_400(self):
        r,_=self.request('EU');self.assertEqual(r.status_code,400)
    def test_assessment_last(self):
        c,t=course();c.course_id='unreviewed';t.source_metadata.raw_fees={}
        models.Course.objects.courses=[c,self.c]
        _,d=self.request('international');self.assertEqual(d['results'][-1]['course_id'],'unreviewed')
    def test_scottish_no_intl_scholarship(self):
        _,d=self.request('scotland');self.assertEqual(d['results'][0]['net_tuition'],1820)
        self.assertEqual(awards.calls,[])

if __name__=='__main__': unittest.main()

"""End-to-end regression against the two user-supplied public patent PDFs.

PDFs are intentionally not committed. Set PATENT_REAL_PDF_DIR or follow the fixture
README. Missing PDFs are reported as skipped, never silently treated as passing.
"""
import json
import os
import time
import unittest
from pathlib import Path

FIXTURES = Path(os.environ.get('PATENT_REAL_PDF_DIR', Path(__file__).parent/'tests/fixtures/patents'))
REPORTS = Path(__file__).resolve().parents[2]/'outputs/real-patent-verification'


class RealPatentTests(unittest.TestCase):
    def setUp(self):
        from test_documents import DocumentTests
        self.harness = DocumentTests()
        self.harness.setUp()
        self.addCleanup(self.harness.tearDown)

    def analyze(self, name):
        path = FIXTURES / name
        if not path.is_file():
            self.skipTest(f'Real PDF fixture missing: {path}')
        self.harness.request(method='POST', data=path.read_bytes())
        deadline = time.monotonic()+300
        while time.monotonic()<deadline:
            document = self.harness.request()['document']
            if document['analysis']['status'] not in {'queued','processing'}:
                self.assertEqual(document['analysis']['status'], 'ready', document)
                analysis = document['analysis']
                REPORTS.mkdir(parents=True,exist_ok=True)
                (REPORTS/(path.stem+'.json')).write_text(json.dumps(analysis,ensure_ascii=False,indent=2))
                print(f'\n{name}: {analysis["sectionPages"]}; claims={len(analysis["claims"])}; references={len(analysis["references"])}',flush=True)
                return analysis
            time.sleep(.2)
        self.fail('Real PDF extraction timed out')

    def test_us10211630(self):
        a = self.analyze('US10211630.pdf')
        self.assertEqual(a['jurisdiction'],'US')
        self.assertTrue(a['quality']['passed'])
        self.assertEqual(a['sectionPages'],{'basic':[1,2], 'claims':[20,21,22],
            'description':list(range(10,21)), 'drawings':list(range(3,10))})
        self.assertEqual(a['pages'][19]['sections'],['description','claims'])
        self.assertEqual(a['pages'][11]['section'],'description')
        self.assertIn('DESCRIPTION OF DRAWINGS',a['description'])
        self.assertEqual([c['number'] for c in a['claims']],list(range(1,25)))
        self.assertEqual([c['number'] for c in a['claims'] if c['kind']=='independent'],[1,12,23,24])
        self.assertEqual(a['claims'][5]['dependencies'],[5])
        self.assertEqual(a['claims'][19]['dependencies'],[19])
        self.assertIn('human - occupiable workspace',a['claims'][0]['text'])
        self.assertIn('bus bars',a['claims'][11]['text'])
        self.assertIn('voltage converters',a['claims'][-1]['text'])
        self.assertEqual(a['expectedClaimCount'],24)
        self.assertEqual(a['metadata']['publicationNumber'],'US10211630B1')
        self.assertEqual(a['metadata']['title'],'DATA CENTER WITH LARGE MEDIUM VOLTAGE DOMAIN')
        self.assertEqual(a['metadata']['classification'], 'H02J 3/00 (2006.01)\nH02J 1/00 (2006.01)')
        self.assertNotIn('\n', a['metadata']['abstract'])
        self.assertIn('providing power to computer systems', a['metadata']['abstract'])
        self.assertIn('transforming the high-voltage power', a['metadata']['abstract'])
        self.assertIn('Google',a['metadata']['owner'])
        self.assertEqual(a['metadata']['applicationDate'],'2013-09-27')
        transformer = next(r for r in a['references'] if r['number']=='104')
        self.assertIn('transformer', transformer['name'].lower())
        self.assertTrue(any(m['page']==3 for m in transformer['matches']))

    def test_us_cover_without_text_layer(self):
        import tempfile
        import pymupdf
        source = FIXTURES / 'US10211630.pdf'
        if not source.is_file():
            self.skipTest(f'Real PDF fixture missing: {source}')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'us-image-cover.pdf'
            with pymupdf.open(source) as original, pymupdf.open() as scan:
                page = original[0]
                image = page.get_pixmap(matrix=pymupdf.Matrix(2.5, 2.5), alpha=False)
                scan.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, stream=image.tobytes('png'))
                scan.save(path)
            a = self.analyze(str(path))
        self.assertEqual(a['metadata']['classification'], 'H02J 3/00 (2006.01)\nH02J 1/00 (2006.01)')
        self.assertNotIn('\n', a['metadata']['abstract'])
        self.assertTrue(a['metadata']['abstract'].startswith('A method of providing power to computer systems in a computer data'))
        self.assertIn('transforming the high-voltage power', a['metadata']['abstract'])
        self.assertEqual(a['metadata']['title'], 'DATA CENTER WITH LARGE MEDIUM VOLTAGE DOMAIN')

    def test_cn119853429a(self):
        a = self.analyze('CN119853429A.pdf')
        self.assertEqual(a['jurisdiction'],'CN')
        self.assertTrue(a['quality']['passed'])
        self.assertEqual(a['sectionPages'],{'basic':[1], 'claims':[2,3,4],
            'description':list(range(5,18)), 'drawings':list(range(18,29))})
        self.assertEqual([c['number'] for c in a['claims']],list(range(1,20)))
        self.assertEqual([c['number'] for c in a['claims'] if c['kind']=='independent'],[1])
        self.assertEqual(a['claims'][10]['dependencies'],[10])
        self.assertTrue(a['claims'][-1]['text'].endswith('所述信号电连接件用于传输信号。'))
        self.assertEqual(a['metadata']['title'],'一种高性能电源模块')
        self.assertEqual(a['metadata']['applicationNumber'],'202411446523.9')
        self.assertEqual(a['metadata']['publicationNumber'],'CN119853429A')
        self.assertEqual(a['metadata']['owner'],'上海沛塬电子有限公司')
        reference = next(r for r in a['references'] if r['number']=='10')
        self.assertIn('第一基板',reference['name'])
        self.assertTrue(reference['matches'])


if __name__=='__main__': unittest.main()

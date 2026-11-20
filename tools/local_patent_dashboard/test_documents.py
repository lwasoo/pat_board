"""Real PDF/OCR and HTTP regression tests using generated patent fixtures."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pymupdf
import dashboard_config as config
import dashboard_documents as documents
from dashboard_database import connect, initialize_database, upsert_patent
from dashboard_server import PatentHandler, ThreadingHTTPServer


def sample_pdf(scanned=False):
    pdf = pymupdf.open()
    def page(lines):
        p = pdf.new_page(width=595, height=842)
        for index, text in enumerate(lines):
            p.insert_text((50, 60 + 24*index), text, fontname='china-s', fontsize=12)
        return p
    page(['CN123456789A', '(54) 发明名称', '一种测试装置', '(21) 申请号', 'CN202610000001.1',
          '(71) 申请人', '测试公司', '(72) 发明人', '张三', '(51) 分类号', 'H05K7/20', '(57) 摘要',
          '本发明提供一种测试装置，壳体和连接件相互连接，用于测试专利阅读及识别。'])
    page(['权利要求书', '1. 一种测试装置，包括壳体（1）和连接件（2），其特征在于，',
          '所述壳体（1）与连接件（2）固定连接。',
          '2. 根据权利要求1所述的测试装置，其特征在于，',
          '所述壳体（1）为金属壳体。',
          '3. 一种安装方法，包括安装上述测试装置。'])
    page(['说明书', '一种测试装置', '技术领域', '本发明涉及装置结构领域。',
          '具体实施方式', '壳体（1）和连接件（2）配合连接，形成固定结构。',
          '图1为该测试装置的结构示意图，部件编号1指壳体，编号2指连接件。'])
    p=page(['说明书附图'])
    p.draw_rect(pymupdf.Rect(150, 200, 400, 450), width=2)
    p.draw_line(pymupdf.Point(200, 250),pymupdf.Point(120, 170))
    p.draw_line(pymupdf.Point(380, 240),pymupdf.Point(450, 170))
    p.insert_text((105,160),'1',fontsize=24)
    p.insert_text((440,160),'2',fontsize=24)
    p.insert_text((260,520),'Fig. 1',fontsize=16)
    if not scanned:
        data=pdf.tobytes();pdf.close();return data
    raster=pymupdf.open()
    for p in pdf:
        pix=p.get_pixmap(matrix=pymupdf.Matrix(2,2))
        target=raster.new_page(width=p.rect.width,height=p.rect.height)
        target.insert_image(target.rect,stream=pix.tobytes('png'))
    data=raster.tobytes();raster.close();pdf.close();return data


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        directory=Path(self.temporary.name)
        self.paths=patch.multiple(config,DATA_DIR=directory,DB_PATH=directory/'patents.db',
            NOTIFICATION_CONFIG_PATH=directory/'notification_config.json')
        self.paths.start();initialize_database()
        with closing(connect()) as conn, conn:
            self.id=upsert_patent(conn,{'applicationNumber':'CN-TEST','title':'原台账标题','applicationDate':'2026-01-01'})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),PatentHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        deadline=time.monotonic()+120
        while documents._ACTIVE and time.monotonic()<deadline:time.sleep(.05)
        self.paths.stop();self.temporary.cleanup()

    def request(self,suffix='',method='GET',data=None):
        url=self.base+'/api/patents/document'+suffix+('?id='+str(self.id))
        request=urllib.request.Request(url,data=data,method=method,headers={'Content-Type':'application/pdf' if method=='POST' and not suffix else 'application/json'})
        with urllib.request.urlopen(request,timeout=10) as response:
            body=response.read()
            return json.loads(body) if response.headers['Content-Type'].startswith('application/json') else body

    def wait_ready(self):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            result=self.request()
            if result['document']['analysis']['status'] not in {'queued','processing'}:
                self.assertEqual(result['document']['analysis']['status'],'ready',result)
                return result['document']
            time.sleep(.1)
        self.fail('OCR did not complete')

    def test_text_pdf_upload_classification_linking_and_correction(self):
        self.request(method='POST',data=sample_pdf())
        document=self.wait_ready();a=document['analysis']
        self.assertEqual([p['section'] for p in a['pages']],['basic','claims','description','drawings'])
        self.assertEqual([c['kind'] for c in a['claims']],['independent','dependent','independent'])
        self.assertEqual(a['claims'][1]['dependencies'],[1])
        self.assertIn('壳体',next(r for r in a['references'] if r['number']=='1')['name'])
        reference=next(r for r in a['references'] if r['number']=='1')
        self.assertTrue(any(m['page']==4 for m in reference['matches']))
        self.assertTrue(self.request('/file').startswith(b'%PDF-'))
        self.assertTrue(self.request('/page').startswith(b'\x89PNG'))
        corrected=self.request(method='PATCH',data=json.dumps({'version':document['version'],
            'claimsText':'1. 一种装置，包括壳体（1）。\n2. 根据权利要求1所述的装置。',
            'annotation':{'page':4,'number':'1','box':[.1,.2,.03,.03]}}).encode())
        self.assertTrue(corrected['document']['analysis']['reviewed'])
        self.assertEqual(corrected['document']['analysis']['references'][0]['matches'][0]['source'], 'manual')
        with closing(connect()) as conn:
            self.assertEqual(conn.execute('SELECT title FROM patents WHERE id=?',(self.id,)).fetchone()[0],'原台账标题')
        self.assertTrue(self.request(method='DELETE')['deleted'])
        self.assertIsNone(self.request()['document'])
        self.assertFalse(list((config.DATA_DIR/'documents').glob('*/original.pdf')))

    def test_scanned_pdf_chinese_ocr(self):
        self.assertTrue(documents.capabilities()['ocr'],'Chinese and English Tesseract models are required')
        self.request(method='POST',data=sample_pdf(scanned=True))
        a=self.wait_ready()['analysis']
        self.assertTrue(all(p['ocr'] for p in a['pages']))
        self.assertEqual(len(a['claims']),3,a)
        self.assertEqual(a['claims'][1]['kind'],'dependent')
        self.assertIn('金属壳体', a['claims'][1]['text'])
        ref=next(r for r in a['references'] if r['number']=='1')
        self.assertTrue(ref['matches'],a)

    def test_reject_invalid_pdf_and_stale_corrections(self):
        for data in [b'not a pdf',b'%PDF- invalid']:
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request(method='POST',data=data)
            self.assertEqual(caught.exception.code,400)
        self.assertIsNone(self.request()['document'])
        self.request(method='POST',data=sample_pdf());self.wait_ready()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(method='PATCH',data=json.dumps({'version':'stale','claimsText':'1. test'}).encode())
        self.assertEqual(caught.exception.code,400)


if __name__=='__main__':unittest.main()

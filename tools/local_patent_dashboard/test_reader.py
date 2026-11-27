"""Library isolation, PPUBS protocol, version matching and offline-cache regressions."""
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch

import dashboard_documents as documents
import dashboard_ppubs as ppubs
import dashboard_reader as reader
from dashboard_database import connect
import test_backend as backend
from test_documents import sample_pdf

NUMBER = 'US20250337340A1'
RAW = {
    'guid': 'US-20250337340-A1', 'inventionTitle': 'Power supply',
    'abstractHtml': '<p>A stable power supply.</p>',
    'claimsHtml': '<b>1</b>. A power system with a converter.<br><b>2</b>. The system of claim 1, with a transformer.',
    'descriptionHtml': '<p>[0001] A converter supplies power.</p><p>[0002] A transformer connects to it.</p>',
    'ipcCodeFlattened': ['H02J 3/00'],
}


class PpubsTests(unittest.TestCase):
    def test_matching_number_and_structured_text(self):
        text = ppubs.convert_document(RAW, NUMBER)
        self.assertEqual(text['metadata']['publicationNumber'], NUMBER)
        self.assertEqual([c['kind'] for c in text['claims']], ['independent', 'dependent'])
        self.assertEqual(text['claims'][1]['dependencies'], [1])
        self.assertIn('[0002]', text['description'])
        self.assertEqual(ppubs.publication_number('US 10,211,630 B1'), 'US10211630B1')
        self.assertEqual(ppubs.html_text('<script>alert(1)</script><p>&lt;safe&gt;</p>'), '<safe>')
        with self.assertRaisesRegex(ValueError, '其他公开号'):
            ppubs.convert_document(RAW, 'US20250337340B2')
        with self.assertRaisesRegex(ValueError, '完整权利要求'):
            ppubs.convert_document({**RAW, 'claimsHtml': ''}, NUMBER)
        with self.assertRaises(ValueError):
            ppubs.publication_number('https://example.com/arbitrary')

    def test_description_keeps_html_paragraphs_and_source_numbers(self):
        raw = {**RAW, 'descriptionHtml': '<h3>BACKGROUND</h3><p num="0003">[0003]First line\ncontinued <b>in bold</b>.</p><p num="0004">Second paragraph.</p><script>bad()</script>'}
        text = ppubs.convert_document(raw, NUMBER)['description']
        self.assertEqual(text, 'BACKGROUND\n\n[0003]First line continued in bold.\n\n[0004] Second paragraph.')
        self.assertEqual(ppubs.description_text('First paragraph.<br/>Second paragraph.'), 'First paragraph.\n\nSecond paragraph.')
        self.assertEqual(ppubs.html_text('<b>1</b>. A system.<br><b>2</b>. The system of claim 1.'), '1. A system.\n2. The system of claim 1.')

    def test_public_session_request_flow_without_api_key(self):
        observed = []
        hits = [{'guid': RAW['guid'], 'type': 'US-PGPUB'}]
        pdf_bytes = sample_pdf()
        class Fake(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, data, token=False):
                body = json.dumps(data).encode()
                self.send_response(200)
                if token:
                    self.send_header('X-Access-Token', 'temporary-test-session')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                observed.append(self.path)
                if self.path == '/pubwebapp/':
                    return self.reply({})
                if self.path.startswith('/api/print/save/'):
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(pdf_bytes)))
                    self.end_headers()
                    self.wfile.write(pdf_bytes)
                    return
                self.reply({**RAW, 'pageCount': 4, 'imageLocation': 'pgpub/2025/test'})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append((self.path, data, self.headers.get('X-Access-Token')))
                if self.path.endswith('/session'):
                    return self.reply({'userCase': {'caseId': 123}}, True)
                if self.headers.get('X-Access-Token') != 'temporary-test-session':
                    self.send_error(401)
                    return
                if self.path == '/api/print/imageviewer':
                    assert data['pageKeys'] == [f'pgpub/2025/test/{n:08}.tif' for n in range(1, 5)]
                    assert data['patentGuid'] == RAW['guid']
                    return self.reply('job-1')
                if self.path == '/api/print/print-process':
                    assert data == ['job-1']
                    return self.reply([{'printStatus': 'COMPLETED', 'pdfName': 'original.pdf'}])
                self.reply({'numFound': len(hits), 'patents': hits})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(ppubs, 'BASE', f'http://127.0.0.1:{server.server_port}'):
                client = ppubs.PpubsClient()
                result = client.fetch(NUMBER)
                self.assertEqual(client.download_pdf(NUMBER), pdf_bytes)
                self.assertEqual(observed.count('/pubwebapp/'), 1)
                partial = ppubs.PpubsClient().fetch('20250337340')
                self.assertEqual(partial['source']['publicationNumber'], NUMBER)
                hits.append({'guid': 'US-20250337340-A9', 'type': 'US-PGPUB'})
                with self.assertRaises(ppubs.PublicationChoices) as choices:
                    ppubs.PpubsClient().fetch('US20250337340')
                self.assertEqual(len(choices.exception.candidates), 2)
                self.assertEqual(ppubs.PpubsClient().download_pdf(NUMBER), pdf_bytes)
            self.assertEqual(result['metadata']['title'], 'Power supply')
            search = next(entry[1] for entry in observed if isinstance(entry, tuple) and entry[0].endswith('/searchWithBeFamily'))
            self.assertEqual(search['query']['caseId'], 123)
            self.assertEqual(search['query']['q'], '"20250337340".pn.')
        finally:
            server.shutdown(); server.server_close(); thread.join()


class ReaderTests(unittest.TestCase):
    setUp = backend.BackendTests.setUp
    stop_server = backend.BackendTests.stop_server
    request = backend.BackendTests.request
    def wait_text(self, item_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.request(f'/api/reader/fulltext?id={item_id}')
            if result['status'] != 'fetching':
                return result
            time.sleep(.03)
        self.fail('Full-text worker did not finish')

    def test_partial_number_selection_and_pdf_attachment(self):
        item_id = self.request('/api/reader/items', 'POST', {'publicationNumber': 'US20250337340'})['item']['id']
        with patch.object(reader.PpubsClient, 'fetch', side_effect=ppubs.PublicationChoices([
                {'publicationNumber': NUMBER}, {'publicationNumber': 'US20250337340A9'}])):
            self.request(f'/api/reader/fulltext?id={item_id}', 'POST', {'publicationNumber': '20250337340'})
            self.assertEqual(self.wait_text(item_id)['status'], 'select_version')
        with patch.object(reader.PpubsClient, 'fetch', return_value=ppubs.convert_document(RAW, NUMBER)):
            self.request(f'/api/reader/fulltext?id={item_id}', 'POST', {'publicationNumber': NUMBER})
            self.assertEqual(self.wait_text(item_id)['status'], 'ready')
        self.assertEqual(self.request(f'/api/reader/item?id={item_id}')['item']['publicationNumber'], NUMBER)
        with patch.object(reader.PpubsClient, 'download_pdf', return_value=sample_pdf()), patch.object(documents, 'ocr_command', return_value=[]):
            self.request(f'/api/reader/pdf?id={item_id}', 'POST')
            reader._WORKER.submit(lambda: None).result(timeout=5)
            self.assertEqual(self.request(f'/api/reader/pdf?id={item_id}')['status'], 'ready')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                doc = self.request(f'/api/reader/document?id={item_id}')['document']
                if doc['analysis']['status'] not in {'queued', 'processing'}:
                    break
                time.sleep(.03)
        self.assertTrue(any(p['section'] == 'drawings' for p in doc['analysis']['pages']))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(f'/api/reader/pdf?id={item_id}', 'POST')
        self.assertEqual(caught.exception.code, 400)

    def test_downloaded_scan_defers_header_and_label_ocr(self):
        item_id = reader.create_item({'publicationNumber': NUMBER})['id']
        text = ppubs.convert_document({**RAW, 'descriptionHtml': 'A transformer (104).'}, NUMBER)
        scan = sample_pdf(scanned=True)
        with patch.object(documents, 'ocr_page') as ocr:
            saved = documents.upload_document(item_id, scan, 'original.pdf', True, online_text=text)['document']
            documents._WORKER.submit(lambda: None).result(timeout=5)
            self.assertEqual(documents.document_file(item_id, library=True)[0], scan)
            def analysis():
                return self.request(f'/api/reader/document?id={item_id}')['document']['analysis']
            self.assertEqual(analysis()['status'], 'ready')
            self.assertEqual(analysis()['mode'], 'drawings')
            ocr.assert_not_called()
            def run(task):
                self.request(f'/api/reader/document/drawing-ocr?id={item_id}', 'POST',
                             {'version': saved['version'], 'task': task})
                documents._WORKER.submit(lambda: None).result(timeout=5)
            ocr.side_effect = [('', []), ('', []), ('Sheet 1 of 1', [])]
            run('sheets')
            self.assertEqual([p['number'] for p in analysis()['pages'] if p['section'] == 'drawings'], [4])
            self.assertEqual(ocr.call_count, 3)
            for call in ocr.call_args_list:
                self.assertLess(call.args[0].height, call.args[0].width)
            label = {'text': '104', 'source': 'ocr', 'box': [.3, .4, .1, .1], 'confidence': 92}
            ocr.side_effect = [('', [label]), ('', [])]
            run('labels')
            self.assertEqual(analysis()['references'][0]['matches'][0]['page'], 4)
            run('labels')
            self.assertEqual(ocr.call_count, 5)
            with self.assertRaises(urllib.error.HTTPError):
                self.request(f'/api/reader/document/drawing-ocr?id={item_id}', 'POST',
                             {'version': 'old-file', 'task': 'labels'})

    def test_open_cached_text_only_downloads_missing_pdf_once(self):
        item_id = reader.create_item({'publicationNumber': NUMBER})['id']
        text = ppubs.convert_document(RAW, NUMBER)
        reader._save_cache(f'library:{item_id}', {'status': 'ready', 'data': text})
        with patch.object(reader.PpubsClient, 'fetch') as fetch, patch.object(
                reader.PpubsClient, 'download_pdf', side_effect=ValueError('无图像')) as download:
            for _ in range(2):
                self.assertEqual(self.request(f'/api/reader/prepare?id={item_id}', 'POST')['data'], text)
                reader._WORKER.submit(lambda: None).result(timeout=5)
            self.assertEqual(self.request(f'/api/reader/pdf?id={item_id}')['status'], 'failed')
            download.assert_called_once_with(NUMBER)
            fetch.assert_not_called()

    def test_open_with_existing_pdf_only_fetches_text(self):
        item_id = reader.create_item({'publicationNumber': NUMBER})['id']
        with patch.object(documents, 'ocr_command', return_value=[]):
            documents.upload_document(item_id, sample_pdf(), '已有原文.pdf', True)
            with patch.object(reader.PpubsClient, 'fetch', return_value=ppubs.convert_document(RAW, NUMBER)) as fetch, patch.object(
                    reader.PpubsClient, 'download_pdf') as download:
                for _ in range(2):
                    self.request(f'/api/reader/prepare?id={item_id}', 'POST')
                self.assertEqual(self.wait_text(item_id)['status'], 'ready')
                reader._WORKER.submit(lambda: None).result(timeout=5)
                self.assertEqual(documents.document_record(item_id, True)['file_name'], '已有原文.pdf')
                fetch.assert_called_once_with(NUMBER)
                download.assert_not_called()
            documents._WORKER.submit(lambda: None).result(timeout=10)

    def test_open_non_us_or_unnumbered_item_stays_local(self):
        with patch.object(reader.PpubsClient, 'fetch') as fetch:
            for number in ['', 'CN119853429A']:
                item_id = reader.create_item({'title': '本地文献', 'publicationNumber': number})['id']
                self.assertEqual(self.request(f'/api/reader/prepare?id={item_id}', 'POST')['status'], 'empty')
            reader._WORKER.submit(lambda: None).result(timeout=5)
            fetch.assert_not_called()

    def test_failed_pdf_download_preserves_cached_text(self):
        item_id = self.request('/api/reader/items', 'POST', {'publicationNumber': NUMBER})['item']['id']
        text = ppubs.convert_document(RAW, NUMBER)
        with patch.object(reader.PpubsClient, 'fetch', return_value=text):
            self.request(f'/api/reader/fulltext?id={item_id}', 'POST', {'publicationNumber': NUMBER})
            self.wait_text(item_id)
        with patch.object(reader.PpubsClient, 'download_pdf', side_effect=ValueError('PDF service unavailable')):
            self.request(f'/api/reader/pdf?id={item_id}', 'POST')
            reader._WORKER.submit(lambda: None).result(timeout=5)
        self.assertEqual(self.request(f'/api/reader/pdf?id={item_id}')['status'], 'failed')
        self.assertEqual(self.request(f'/api/reader/fulltext?id={item_id}')['data']['claims'], text['claims'])
        self.assertIsNone(self.request(f'/api/reader/document?id={item_id}')['document'])

    def test_new_library_has_no_ledger_or_fee_requirement(self):
        item_id = self.request('/api/reader/items', 'POST', {'title': 'Independent document'})['item']['id']
        with patch.object(documents, 'ocr_command', return_value=[]):
            request = urllib.request.Request(self.base + f'/api/reader/document?id={item_id}', data=sample_pdf(), method='POST')
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 202)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                doc = self.request(f'/api/reader/document?id={item_id}')['document']
                if doc['analysis']['status'] not in {'queued', 'processing'}:
                    break
                time.sleep(.03)
        self.assertEqual(doc['analysis']['status'], 'ready')
        self.assertEqual(len(doc['analysis']['claims']), 3)
        with closing(connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM patents').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM fees').fetchone()[0], 0)
        self.assertEqual(self.request('/api/reader/items')['items'][0]['scope'], 'library')
        self.request(f'/api/reader/items?id={item_id}', 'DELETE')
        self.assertEqual(self.request('/api/reader/items')['items'], [])

    def test_cached_text_survives_upstream_failure_and_restart(self):
        item_id = self.request('/api/reader/items', 'POST', {'publicationNumber': NUMBER})['item']['id']
        text = ppubs.convert_document(RAW, NUMBER)
        with patch.object(reader.PpubsClient, 'fetch', return_value=text):
            self.request(f'/api/reader/fulltext?id={item_id}', 'POST', {'publicationNumber': NUMBER})
            self.assertEqual(self.wait_text(item_id)['status'], 'ready')
        with patch.object(reader.PpubsClient, 'fetch', side_effect=ValueError('network unavailable')):
            self.request(f'/api/reader/fulltext?id={item_id}', 'POST', {'publicationNumber': NUMBER})
            failed = self.wait_text(item_id)
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['data']['claims'], text['claims'])
        with patch.object(reader.PpubsClient, 'fetch') as fetch:
            reader._JOBS.clear()
            self.assertEqual(self.request(f'/api/reader/fulltext?id={item_id}')['data']['description'], text['description'])
            fetch.assert_not_called()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(f'/api/reader/fulltext?id={item_id}', 'POST', {'publicationNumber': 'US10211630B1'})
        self.assertEqual(caught.exception.code, 400)

    def test_deleted_item_does_not_resurrect_from_inflight_fetch(self):
        item_id = self.request('/api/reader/items', 'POST', {'publicationNumber': NUMBER})['item']['id']
        entered, finish = threading.Event(), threading.Event()
        def delayed(number):
            entered.set(); finish.wait(3)
            return ppubs.convert_document(RAW, NUMBER)
        with patch.object(reader.PpubsClient, 'fetch', side_effect=delayed):
            self.request(f'/api/reader/fulltext?id={item_id}', 'POST', {'publicationNumber': NUMBER})
            self.assertTrue(entered.wait(2))
            self.request(f'/api/reader/items?id={item_id}', 'DELETE')
            finish.set()
            reader._WORKER.submit(lambda: None).result(timeout=3)
        with closing(connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM reader_fulltexts').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()

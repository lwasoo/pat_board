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

    def test_public_session_request_flow_without_api_key(self):
        observed = []
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
                self.reply(RAW)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append((self.path, data, self.headers.get('X-Access-Token')))
                if self.path.endswith('/session'):
                    return self.reply({'userCase': {'caseId': 123}}, True)
                if self.headers.get('X-Access-Token') != 'temporary-test-session':
                    self.send_error(401)
                    return
                self.reply({'numFound': 1, 'patents': [{'guid': RAW['guid'], 'type': 'US-PGPUB'}]})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Fake)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(ppubs, 'BASE', f'http://127.0.0.1:{server.server_port}'):
                result = ppubs.PpubsClient().fetch(NUMBER)
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

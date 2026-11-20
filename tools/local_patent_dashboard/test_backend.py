"""HTTP and persistence regression tests for the backend module boundaries."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import dashboard_config as config
from dashboard_database import connect, initialize_database
from dashboard_notifications import scheduled_command
from dashboard_server import PatentHandler, ThreadingHTTPServer


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        self.paths = patch.multiple(
            config, DATA_DIR=directory, DB_PATH=directory / 'patents.db',
            NOTIFICATION_CONFIG_PATH=directory / 'notification_config.json',
        )
        self.paths.start()
        self.addCleanup(self.paths.stop)
        initialize_database()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), PatentHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method='GET', payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_patent_and_fee_lifecycle(self):
        self.assertTrue(self.request('/health')['ok'])
        payload = {'applicationNumber': 'CN-HTTP-TEST', 'title': '测试专利',
                   'applicationDate': '2026-01-01', 'inventors': '张三'}
        patent = self.request('/api/patents', 'POST', payload)['patent']
        patent_id = patent['id']
        payload['title'] = '修改后的专利'
        updated = self.request(f'/api/patents?id={patent_id}', 'PUT', payload)
        self.assertEqual(updated['patent']['title'], payload['title'])
        self.assertEqual(self.request('/api/patents')['patents'][0]['inventors'], '张三')
        fee = {'patentId': patent_id, 'feeType': '年费', 'feeRegion': '中国',
               'dueDate': '2027-01-01', 'amount': 100, 'status': '待缴费'}
        fee_id = self.request('/api/fees', 'POST', fee)['id']
        fee['amount'] = 200
        self.assertTrue(self.request(f'/api/fees?id={fee_id}', 'PATCH', fee)['updated'])
        self.assertEqual(self.request('/api/fees')['fees'][0]['amount'], 200)
        self.assertEqual(self.request(f'/api/patents?id={patent_id}', 'DELETE')['deleted'], 1)
        self.assertEqual(self.request('/api/patents')['patents'], [])
        self.assertEqual(self.request('/api/fees')['fees'], [])

    def test_assets_workbooks_and_notification_status(self):
        for path in ['/', '/app.js', '/style.css']:
            with self.subTest(path=path):
                with urllib.request.urlopen(self.base + path, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.read())
        for path in ['/api/patents/template', '/api/patents/export']:
            with self.subTest(path=path):
                with urllib.request.urlopen(self.base + path, timeout=5) as response:
                    data = response.read()
                with zipfile.ZipFile(io.BytesIO(data)) as workbook:
                    self.assertIsNone(workbook.testzip())
                    self.assertIn('xl/workbook.xml', workbook.namelist())
        self.assertFalse(self.request('/api/notifications')['enabled'])
        result = self.request('/api/notifications/config', 'POST', {
            'enabled': False, 'scheduleTime': '17:30',
        })
        self.assertFalse(result['enabled'])
        self.assertEqual(self.request('/api/notifications')['scheduleTime'], '17:30')

    def test_database_reinitialization_preserves_data(self):
        self.request('/api/patents', 'POST', {
            'applicationNumber': 'CN-PERSIST', 'title': '持久化测试',
        })
        initialize_database()
        with closing(connect()) as connection:
            self.assertEqual(connection.execute('SELECT title FROM patents').fetchone()[0], '持久化测试')
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        # Source-mode scheduled tasks must still execute the original entry point.
        self.assertIn(str(config.APP_SCRIPT), scheduled_command())
        self.assertEqual(config.APP_SCRIPT.name, 'local_dashboard.py')

    def test_invalid_patent_rolls_back(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/patents', 'POST', {'applicationNumber': 'INVALID'})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.request('/api/patents')['patents'], [])

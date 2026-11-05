import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import local_dashboard as dashboard


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_paths = dashboard.DATA_DIR, dashboard.DB_PATH, dashboard.NOTIFICATION_CONFIG_PATH
        dashboard.DATA_DIR = Path(self.temporary.name)
        dashboard.DB_PATH = dashboard.DATA_DIR / "patents.db"
        dashboard.NOTIFICATION_CONFIG_PATH = dashboard.DATA_DIR / "notification_config.json"
        dashboard.initialize_database()

    def tearDown(self):
        dashboard.DATA_DIR, dashboard.DB_PATH, dashboard.NOTIFICATION_CONFIG_PATH = self.old_paths
        self.temporary.cleanup()

    def test_webhook_validation_rejects_non_wecom_hosts(self):
        with self.assertRaises(ValueError):
            dashboard.validate_wecom_webhook("https://example.com/cgi-bin/webhook/send?key=test")

    def test_due_reminder_is_sent_once_per_stage(self):
        with dashboard.connect() as connection:
            patent_id = connection.execute("SELECT id FROM patents LIMIT 1").fetchone()[0]
            due_date = (datetime.now().date() + timedelta(days=6)).isoformat()
            connection.execute(
                "INSERT INTO fees(patent_id,fee_type,due_date,status) VALUES(?,?,?,'待缴费')",
                (patent_id, "年费", due_date),
            )
        dashboard.save_notification_config({
            "enabled": True,
            "scheduleTime": "09:00",
            "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        })
        messages = []
        with patch.object(dashboard, "send_wecom_markdown", side_effect=lambda _, body: messages.append(body)):
            first = dashboard.run_due_notifications()
            second = dashboard.run_due_notifications()
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(messages), 1)
        self.assertIn("剩余 6 天", messages[0])

    def test_inventors_survive_database_and_excel_round_trip(self):
        payload = {
            "applicationNumber": "TEST-INVENTOR-001",
            "title": "发明人字段测试",
            "countryRegion": "美国",
            "patentType": "发明",
            "applicationDate": "2026-09-16",
            "publicationDate": "2027-01-10",
            "expirationDate": "2046-09-16",
            "legalStatus": "审中",
            "inventors": "张三、李四",
            "agentReference": "PY-US-TEST",
            "claimCount": 18,
            "independentClaimCount": 2,
            "pageCount": 56,
            "cnDraftStatus": "获证",
            "foreignDraftStatus": "OA1",
            "relatedNumbers": "EP: EP123456A1",
            "caseNotes": "分案备注",
        }
        with dashboard.connect() as connection:
            patent_id = dashboard.upsert_patent(connection, payload)
            row = dict(connection.execute(dashboard.SELECT_SQL + " WHERE p.id=?", (patent_id,)).fetchone())
        self.assertEqual(row["inventors"], "张三、李四")
        self.assertEqual(row["agentReference"], "PY-US-TEST")
        self.assertEqual(row["claimCount"], 18)
        parsed = dashboard.parse_xlsx(dashboard.build_xlsx([row], []))
        self.assertEqual(parsed[0]["inventors"], "张三、李四")
        self.assertEqual(parsed[0]["countryRegion"], "美国")
        self.assertEqual(parsed[0]["expirationDate"], "2046-09-16")
        self.assertEqual(parsed[0]["caseNotes"], "分案备注")
        _, parsed_fees = dashboard.parse_import_xlsx(dashboard.build_xlsx([row], [{
            "applicationNumber": row["applicationNumber"], "patentTitle": row["title"],
            "feeRegion": "美国", "feeType": "第一年年费", "dueDate": "2027-09-16",
            "amount": 516, "rawValue": "516", "currency": "USD", "status": "待缴费",
            "paidDate": "", "note": "测试费用",
        }]))
        self.assertEqual(parsed_fees[0]["feeRegion"], "美国")
        self.assertEqual(parsed_fees[0]["amount"], 516.0)

    def test_legacy_fee_sheet_becomes_idempotent_fee_records(self):
        headers = [""] * 88
        headers[3] = "申请号"
        headers[9] = "发明专利申请费"
        headers[36] = "中代理费\nRMB"
        headers[46] = "后补文件"
        row = [""] * 88
        row[3] = "CN202510256707.7"
        row[9] = "135"
        row[36] = "5000"
        row[46] = "2026-01-24"
        fees = dashboard.parse_legacy_fees([headers, row])
        self.assertEqual(len(fees), 3)
        self.assertEqual(fees[0]["feeRegion"], "中国")
        self.assertEqual(fees[0]["amount"], 135.0)
        self.assertEqual(fees[2]["paidDate"], "2026-01-24")
        with closing(dashboard.connect()) as connection:
            patent_id = connection.execute(
                "SELECT id FROM patents WHERE application_number='CN202510256707.7'"
            ).fetchone()[0]
            for fee in fees:
                dashboard.upsert_imported_fee(connection, patent_id, fee)
                dashboard.upsert_imported_fee(connection, patent_id, fee)
            connection.commit()
            count = connection.execute("SELECT COUNT(*) FROM fees WHERE source='excel'").fetchone()[0]
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()

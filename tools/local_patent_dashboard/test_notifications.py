import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import dashboard_config as config
import dashboard_database as database
import dashboard_excel as excel
import dashboard_notifications as notifications


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_paths = config.DATA_DIR, config.DB_PATH, config.NOTIFICATION_CONFIG_PATH
        config.DATA_DIR = Path(self.temporary.name)
        config.DB_PATH = config.DATA_DIR / "patents.db"
        config.NOTIFICATION_CONFIG_PATH = config.DATA_DIR / "notification_config.json"
        database.initialize_database()
        # Tests own their fixtures; production databases intentionally start empty.
        with closing(database.connect()) as connection:
            database.upsert_patent(connection, {
                "applicationNumber": "CN202510256707.7", "title": "测试专利",
                "applicationDate": "2025-01-01",
            })
            connection.commit()

    def tearDown(self):
        config.DATA_DIR, config.DB_PATH, config.NOTIFICATION_CONFIG_PATH = self.old_paths
        self.temporary.cleanup()

    def test_webhook_validation_rejects_non_wecom_hosts(self):
        with self.assertRaises(ValueError):
            notifications.validate_wecom_webhook("https://example.com/cgi-bin/webhook/send?key=test")

    def test_due_reminder_is_sent_once_per_stage(self):
        with database.connect() as connection:
            patent_id = connection.execute("SELECT id FROM patents LIMIT 1").fetchone()[0]
            due_date = (datetime.now().date() + timedelta(days=6)).isoformat()
            connection.execute(
                "INSERT INTO fees(patent_id,fee_type,due_date,status) VALUES(?,?,?,'待缴费')",
                (patent_id, "年费", due_date),
            )
        notifications.save_notification_config({
            "enabled": True,
            "scheduleTime": "09:00",
            "webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
        })
        messages = []
        with patch.object(notifications, "send_wecom_markdown", side_effect=lambda _, body: messages.append(body)):
            first = notifications.run_due_notifications()
            second = notifications.run_due_notifications()
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
        with database.connect() as connection:
            patent_id = database.upsert_patent(connection, payload)
            row = dict(connection.execute(database.SELECT_SQL + " WHERE p.id=?", (patent_id,)).fetchone())
        self.assertEqual(row["inventors"], "张三、李四")
        self.assertEqual(row["agentReference"], "PY-US-TEST")
        self.assertEqual(row["claimCount"], 18)
        parsed = excel.parse_xlsx(excel.build_xlsx([row], []))
        self.assertEqual(parsed[0]["inventors"], "张三、李四")
        self.assertEqual(parsed[0]["countryRegion"], "美国")
        self.assertEqual(parsed[0]["expirationDate"], "2046-09-16")
        self.assertEqual(parsed[0]["caseNotes"], "分案备注")
        _, parsed_fees = excel.parse_import_xlsx(excel.build_xlsx([row], [{
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
        fees = excel.parse_legacy_fees([headers, row])
        self.assertEqual(len(fees), 3)
        self.assertEqual(fees[0]["feeRegion"], "中国")
        self.assertEqual(fees[0]["amount"], 135.0)
        self.assertEqual(fees[2]["paidDate"], "2026-01-24")
        with closing(database.connect()) as connection:
            patent_id = connection.execute(
                "SELECT id FROM patents WHERE application_number='CN202510256707.7'"
            ).fetchone()[0]
            for fee in fees:
                database.upsert_imported_fee(connection, patent_id, fee)
                database.upsert_imported_fee(connection, patent_id, fee)
            connection.commit()
            count = connection.execute("SELECT COUNT(*) FROM fees WHERE source='excel'").fetchone()[0]
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()

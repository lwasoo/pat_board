"""HTTP request handling and application lifecycle."""
from __future__ import annotations

import json
import zipfile
import xml.etree.ElementTree as ET
import mimetypes
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import dashboard_config as settings
from dashboard_config import APP_NAME, MAX_XLSX_SIZE, load_config
from dashboard_database import (
    SELECT_SQL, connect, initialize_database, upsert_patent, upsert_imported_fee,
)
from dashboard_excel import build_xlsx, parse_import_xlsx
from dashboard_notifications import (
    normalized_notification_config, configure_notification_task,
    save_notification_config, notification_status, load_notification_config,
    validate_wecom_webhook, send_wecom_markdown, run_due_notifications,
)
from dashboard_validation import clean_text, normalized_patent
from dashboard_document_routes import DocumentRoutes
from dashboard_reader_routes import ReaderRoutes
from dashboard_documents import remove_managed_document, capabilities


class PatentHandler(ReaderRoutes, DocumentRoutes, BaseHTTPRequestHandler):
    server_version = "LocalPatentDashboard/1.0"

    def log_message(self, _format, *_args):
        return

    def json_response(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def bytes_response(self, data: bytes, content_type: str, file_name: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(file_name)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > 5 * 1024 * 1024:
            raise ValueError("请求内容过大。")
        payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象。")
        return payload

    def route(self):
        return urllib.parse.urlsplit(self.path)

    def do_GET(self):
        if self.handle_reader_route("GET"):
            return
        if self.handle_document_route("GET"):
            return
        route = self.route()
        if route.path == "/health":
            return self.json_response({"ok": True, "mode": "local", "database": str(settings.DB_PATH.name)})
        if route.path == "/api/patents":
            with connect() as conn:
                rows = [dict(row) for row in conn.execute(SELECT_SQL + " ORDER BY p.application_date DESC, p.id DESC")]
            return self.json_response({"patents": rows})
        if route.path == "/api/patents/template":
            return self.bytes_response(build_xlsx([], [], True), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "专利导入模板.xlsx")
        if route.path == "/api/patents/export":
            requested = {int(v) for v in urllib.parse.parse_qs(route.query).get("ids", [""])[0].split(",") if v.isdigit()}
            with connect() as conn:
                patents = [dict(row) for row in conn.execute(SELECT_SQL + " ORDER BY p.application_date DESC, p.id DESC")]
                if requested:
                    patents = [row for row in patents if row["id"] in requested]
                selected = {row["id"] for row in patents}
                fees = [dict(row) for row in conn.execute("""SELECT f.id, f.patent_id AS patentId,
                p.application_number AS applicationNumber, p.title AS patentTitle, f.fee_type AS feeType,
                f.fee_region AS feeRegion, f.due_date AS dueDate, f.amount, f.raw_value AS rawValue,
                f.currency, f.status, f.paid_date AS paidDate, f.note
                FROM fees f JOIN patents p ON p.id=f.patent_id ORDER BY f.due_date""") if row["patentId"] in selected]
            stamp = datetime.now().strftime("%Y%m%d")
            return self.bytes_response(build_xlsx(patents, fees), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"专利台账导出-{stamp}.xlsx")
        if route.path == "/api/fees":
            with connect() as conn:
                rows = [dict(row) for row in conn.execute("""SELECT f.id, f.patent_id AS patentId,
                p.application_number AS applicationNumber, p.title AS patentTitle,
                f.fee_type AS feeType, f.fee_region AS feeRegion, f.due_date AS dueDate,
                f.amount, f.raw_value AS rawValue, f.currency,
                f.status, f.paid_date AS paidDate, f.note
                FROM fees f JOIN patents p ON p.id=f.patent_id
                ORDER BY CASE f.status WHEN '已逾期' THEN 0 WHEN '待缴费' THEN 1 ELSE 2 END, f.due_date, f.id""")]
            return self.json_response({"fees": rows})
        if route.path == "/api/notifications":
            return self.json_response(notification_status())
        return self.send_static(route.path)

    def do_POST(self):
        if self.handle_reader_route("POST"):
            return
        if self.handle_document_route("POST"):
            return
        route = self.route()
        try:
            if route.path == "/api/notifications/config":
                config = normalized_notification_config(self.read_json())
                task = configure_notification_task(config["enabled"], config["scheduleTime"])
                save_notification_config(config)
                return self.json_response({**notification_status(), **task})
            if route.path == "/api/notifications/test":
                payload = self.read_json()
                config = load_notification_config()
                webhook = validate_wecom_webhook(payload.get("webhook") or config.get("webhook", ""))
                send_wecom_markdown(webhook, "## 企业专利统计看板\n> 企业微信提醒配置成功。")
                return self.json_response({"sent": True, "message": "测试消息已发送。"})
            if route.path == "/api/notifications/run":
                return self.json_response(run_due_notifications())
            if route.path == "/api/patents/import":
                payload = self.read_json()
                rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
                if not rows:
                    raise ValueError("没有可导入的记录。")
                saved = []
                with connect() as conn:
                    for row in rows[:500]:
                        patent_id = upsert_patent(conn, row, clean_text(payload.get("source"), "import"))
                        saved.append({"id": patent_id, "applicationNumber": clean_text(row.get("applicationNumber"))})
                return self.json_response({"imported": len(saved), "patents": saved})
            if route.path == "/api/patents/excel":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > MAX_XLSX_SIZE:
                    raise ValueError("Excel 文件为空或超过 10 MB。")
                rows, fee_rows = parse_import_xlsx(self.rfile.read(length))
                if not rows and not fee_rows:
                    raise ValueError("未在专利或费用工作表中找到可导入的数据。")
                if len(rows) > 5000:
                    raise ValueError("单次最多导入 5000 条专利记录。")
                if len(fee_rows) > 20000:
                    raise ValueError("单次最多导入 20000 条费用记录。")
                checked = []
                errors = []
                for index, row in enumerate(rows, 2):
                    try:
                        checked.append(normalized_patent(row))
                    except ValueError as exc:
                        errors.append(f"第 {index} 行：{exc}")
                if errors:
                    return self.json_response({"error": "；".join(errors[:10]), "errorCount": len(errors)}, 400)
                saved = []
                imported_fees = 0; skipped_fees = 0
                with connect() as conn:
                    for row in checked:
                        patent_id = upsert_patent(conn, row, "excel")
                        saved.append({"id": patent_id, "applicationNumber": row["applicationNumber"]})
                    for fee in fee_rows:
                        patent = conn.execute("SELECT id FROM patents WHERE application_number=?",
                                              (clean_text(fee.get("applicationNumber")),)).fetchone()
                        if not patent:
                            skipped_fees += 1
                            continue
                        upsert_imported_fee(conn, int(patent[0]), fee)
                        imported_fees += 1
                return self.json_response({"imported": len(saved), "feeImported": imported_fees,
                                           "feeSkipped": skipped_fees, "patents": saved})
            if route.path == "/api/patents":
                payload = self.read_json()
                with connect() as conn:
                    patent_id = upsert_patent(conn, payload)
                    row = dict(conn.execute(SELECT_SQL + " WHERE p.id=?", (patent_id,)).fetchone())
                return self.json_response({"patent": row}, 201)
            if route.path == "/api/fees":
                payload = self.read_json()
                patent_id = int(payload.get("patentId") or 0)
                due_date = clean_text(payload.get("dueDate"))
                status = clean_text(payload.get("status"), "待缴费")
                if patent_id <= 0 or (status == "待缴费" and not due_date):
                    raise ValueError("请选择专利；待缴费用还需要填写缴费期限。")
                with connect() as conn:
                    if not conn.execute("SELECT 1 FROM patents WHERE id=?", (patent_id,)).fetchone():
                        raise ValueError("专利记录不存在。")
                    result = conn.execute("""INSERT INTO fees(patent_id,fee_type,fee_region,due_date,amount,raw_value,currency,status,paid_date,note,source)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (patent_id, clean_text(payload.get("feeType"), "年费"),
                    clean_text(payload.get("feeRegion")), due_date, payload.get("amount") if payload.get("amount") is not None else None,
                    clean_text(payload.get("rawValue")), clean_text(payload.get("currency"), "CNY"), status,
                    clean_text(payload.get("paidDate")) or None, clean_text(payload.get("note")), "manual"))
                    fee_id = int(result.lastrowid)
                return self.json_response({"id": fee_id}, 201)
            if route.path == "/api/shutdown":
                self.json_response({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.json_response({"error": "接口不存在。"}, 404)
        except (ValueError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
            self.json_response({"error": str(exc)}, 400)
        except sqlite3.IntegrityError as exc:
            self.json_response({"error": f"数据库保存失败：{exc}"}, 409)
        except Exception as exc:
            self.json_response({"error": f"操作失败：{exc}"}, 500)

    def do_PUT(self):
        if self.handle_reader_route("PUT"):
            return
        route = self.route()
        if route.path != "/api/patents":
            return self.json_response({"error": "接口不存在。"}, 404)
        try:
            patent_id = int(urllib.parse.parse_qs(route.query).get("id", ["0"])[0])
            row = normalized_patent(self.read_json())
            with connect() as conn:
                result = conn.execute("""UPDATE patents SET application_number=?, title=?, country_region=?, patent_type=?, application_date=?,
                publication_date=?, publication_number=?, grant_date=?, expiration_date=?, legal_status=?, department=?, product_line=?,
                family_number=?, family_name=?, inventors=?, owner=?, agent_reference=?, claim_count=?, independent_claim_count=?,
                page_count=?, cn_draft_status=?, foreign_draft_status=?, related_numbers=?, case_notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (row["applicationNumber"], row["title"], row["countryRegion"], row["patentType"], row["applicationDate"],
                 row["publicationDate"] or None, row["publicationNumber"] or None, row["grantDate"] or None,
                 row["expirationDate"] or None, row["legalStatus"],
                 row["department"], row["productLine"], row["familyNumber"] or None,
                 row["familyName"] or None, row["inventors"], row["owner"], row["agentReference"],
                 row["claimCount"], row["independentClaimCount"], row["pageCount"], row["cnDraftStatus"],
                 row["foreignDraftStatus"], row["relatedNumbers"], row["caseNotes"], patent_id))
                if not result.rowcount:
                    return self.json_response({"error": "记录不存在。"}, 404)
                saved = dict(conn.execute(SELECT_SQL + " WHERE p.id=?", (patent_id,)).fetchone())
            self.json_response({"patent": saved})
        except (ValueError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, 400)
        except sqlite3.IntegrityError:
            self.json_response({"error": "申请号已存在。"}, 409)

    def do_PATCH(self):
        if self.handle_reader_route("PATCH"):
            return
        if self.handle_document_route("PATCH"):
            return
        route = self.route()
        if route.path != "/api/fees":
            return self.json_response({"error": "接口不存在。"}, 404)
        try:
            fee_id = int(urllib.parse.parse_qs(route.query).get("id", ["0"])[0])
            payload = self.read_json()
            patent_id = int(payload.get("patentId") or 0)
            due_date = clean_text(payload.get("dueDate"))
            status = clean_text(payload.get("status"), "待缴费")
            if fee_id <= 0 or patent_id <= 0 or (status == "待缴费" and not due_date):
                raise ValueError("缴费记录不完整。")
            with connect() as conn:
                result = conn.execute("""UPDATE fees SET patent_id=?,fee_type=?,fee_region=?,due_date=?,amount=?,raw_value=?,currency=?,status=?,paid_date=?,note=?,source='manual',updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (patent_id, clean_text(payload.get("feeType"), "年费"), clean_text(payload.get("feeRegion")), due_date,
                 payload.get("amount") if payload.get("amount") is not None else None, clean_text(payload.get("rawValue")),
                 clean_text(payload.get("currency"), "CNY"), status, clean_text(payload.get("paidDate")) or None,
                 clean_text(payload.get("note")), fee_id))
                if not result.rowcount:
                    return self.json_response({"error": "缴费记录不存在。"}, 404)
            self.json_response({"updated": True})
        except (ValueError, json.JSONDecodeError) as exc:
            self.json_response({"error": str(exc)}, 400)

    def do_DELETE(self):
        if self.handle_reader_route("DELETE"):
            return
        if self.handle_document_route("DELETE"):
            return
        route = self.route()
        try:
            query = urllib.parse.parse_qs(route.query)
            by_patent = route.path == "/api/fees" and bool(query.get("patentIds"))
            if by_patent:
                raw_ids = query.get("patentIds", [""])[0].split(",")
            else:
                raw_ids = query.get("ids", [""])[0].split(",") if query.get("ids") else query.get("id", ["0"])
            record_ids = sorted({int(value) for value in raw_ids if str(value).isdigit() and int(value) > 0})
            if not record_ids or len(record_ids) > 5000:
                raise ValueError("请选择要删除的记录，单次最多 5000 条。")
            placeholders = ",".join("?" for _ in record_ids)
            documents = []
            with connect() as conn:
                if route.path == "/api/patents":
                    documents = [row[0] for row in conn.execute(
                        f"SELECT object_path FROM patent_documents WHERE patent_id IN ({placeholders})", record_ids)]
                    result = conn.execute(f"DELETE FROM patents WHERE id IN ({placeholders})", record_ids)
                elif route.path == "/api/fees":
                    column = "patent_id" if by_patent else "id"
                    result = conn.execute(f"DELETE FROM fees WHERE {column} IN ({placeholders})", record_ids)
                else:
                    return self.json_response({"error": "接口不存在。"}, 404)
            for object_path in documents:
                try:
                    remove_managed_document(object_path)
                except OSError:
                    pass
            self.json_response({"deleted": int(result.rowcount)})
        except ValueError as exc:
            self.json_response({"error": str(exc) or "编号无效。"}, 400)

    def send_static(self, request_path):
        name = "library.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (settings.STATIC_DIR / name).resolve()
        if settings.STATIC_DIR.resolve() not in path.parents and path != settings.STATIC_DIR.resolve():
            return self.json_response({"error": "文件不存在。"}, 404)
        if not path.exists() or not path.is_file():
            path = settings.STATIC_DIR / "index.html"
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)



def server_url(config: dict) -> str:
    return f"http://127.0.0.1:{config['port']}"



def existing_server(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/health", timeout=1.5) as response:
            return json.loads(response.read().decode("utf-8")).get("mode") == "local"
    except Exception:
        return False



def main() -> None:
    if "--check-runtime" in sys.argv:
        ready = capabilities()
        raise SystemExit(0 if ready["pdf"] and ready["ocr"] else 1)
    if "--check-reminders" in sys.argv:
        initialize_database()
        try:
            result = run_due_notifications()
            if "--quiet" not in sys.argv:
                print(result["message"])
        except Exception as exc:
            if "--quiet" not in sys.argv:
                print(f"提醒检查失败：{exc}", file=sys.stderr)
            raise SystemExit(1)
        return
    config = load_config()
    url = server_url(config)
    if existing_server(url):
        webbrowser.open(url)
        return
    initialize_database()
    notification_config = load_notification_config()
    if notification_config.get("enabled"):
        try:
            configure_notification_task(True, notification_config.get("scheduleTime", "09:00"))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    try:
        server = ThreadingHTTPServer((config["host"], config["port"]), PatentHandler)
    except OSError as exc:
        import tkinter.messagebox as messagebox
        messagebox.showerror(APP_NAME, f"启动失败：端口 {config['port']} 已被占用。\n{exc}")
        return
    if config["open_browser"]:
        threading.Thread(target=lambda: (time.sleep(0.7), webbrowser.open(url)), daemon=True).start()
    server.serve_forever(poll_interval=0.3)
    server.server_close()

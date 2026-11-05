from __future__ import annotations

import json
import io
import html
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


APP_NAME = "企业专利统计看板"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TASK_NAME = "企业专利统计看板-临期提醒"
MAX_XLSX_SIZE = 10 * 1024 * 1024
PATENT_COLUMNS = [
    ("申请号", "applicationNumber"), ("专利名称", "title"), ("国家/地区", "countryRegion"),
    ("专利类型", "patentType"), ("申请日", "applicationDate"), ("公开日", "publicationDate"),
    ("公开号/授权号", "publicationNumber"), ("授权日", "grantDate"), ("到期日", "expirationDate"),
    ("法律状态", "legalStatus"), ("专利族编号", "familyNumber"), ("专利族名称", "familyName"),
    ("发明人/设计人", "inventors"), ("申请人/权利人", "owner"), ("代理编号", "agentReference"),
    ("权利要求项数", "claimCount"), ("独立权利要求项数", "independentClaimCount"),
    ("说明书页数", "pageCount"), ("CN稿状态", "cnDraftStatus"),
    ("境外稿状态", "foreignDraftStatus"), ("部门", "department"), ("产品线", "productLine"),
    ("关联国家编号", "relatedNumbers"), ("备注", "caseNotes"),
]
FEE_COLUMNS = [
    ("关联申请号", "applicationNumber"), ("专利名称", "patentTitle"), ("国家/地区", "feeRegion"),
    ("费用类型", "feeType"), ("缴费期限", "dueDate"), ("金额", "amount"),
    ("原始值", "rawValue"), ("币种", "currency"), ("缴费状态", "status"),
    ("实际缴费日", "paidDate"), ("备注", "note"),
]
FEE_SUMMARY_COLUMNS = [
    ("关联申请号", "applicationNumber"), ("专利名称", "patentTitle"),
    ("中国费用明细", "chinaFees"), ("美国费用明细", "usFees"),
    ("欧洲费用明细", "europeFees"), ("印度费用明细", "indiaFees"),
    ("最近待缴项目", "nextFeeType"), ("最近缴费期限", "nextDueDate"),
    ("最近待缴金额", "nextAmount"), ("币种", "nextCurrency"),
    ("缴费状态", "nextStatus"), ("实际缴费日", "paidDate"), ("备注", "note"),
]
FEE_REGION_SUMMARY_KEYS = {
    "中国": "chinaFees", "美国": "usFees", "欧洲": "europeFees", "印度": "indiaFees",
}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", app_dir())) / name


def load_config() -> dict:
    path = app_dir() / "local_config.json"
    config = {"host": DEFAULT_HOST, "port": DEFAULT_PORT, "open_browser": True}
    if path.exists():
        try:
            config.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    host = str(config.get("host") or DEFAULT_HOST)
    if host not in {"127.0.0.1", "0.0.0.0"}:
        host = DEFAULT_HOST
    try:
        port = int(config.get("port", DEFAULT_PORT))
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not 1024 <= port <= 65535:
        port = DEFAULT_PORT
    return {"host": host, "port": port, "open_browser": bool(config.get("open_browser", True))}


DATA_DIR = app_dir() / "data"
DB_PATH = DATA_DIR / "patents.db"
NOTIFICATION_CONFIG_PATH = DATA_DIR / "notification_config.json"
STATIC_DIR = resource_path("static")


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS patents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  application_number TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  patent_type TEXT NOT NULL,
  country_region TEXT NOT NULL DEFAULT '中国',
  application_date TEXT NOT NULL,
  publication_date TEXT,
  publication_number TEXT,
  grant_date TEXT,
  expiration_date TEXT,
  legal_status TEXT NOT NULL,
  department TEXT NOT NULL DEFAULT '研发部',
  product_line TEXT NOT NULL DEFAULT '未归类',
  family_number TEXT,
  family_name TEXT,
  inventors TEXT NOT NULL DEFAULT '',
  owner TEXT NOT NULL DEFAULT '本公司',
  agent_reference TEXT NOT NULL DEFAULT '',
  claim_count INTEGER,
  independent_claim_count INTEGER,
  page_count INTEGER,
  cn_draft_status TEXT NOT NULL DEFAULT '',
  foreign_draft_status TEXT NOT NULL DEFAULT '',
  related_numbers TEXT NOT NULL DEFAULT '',
  case_notes TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_patents_application_date ON patents(application_date);
CREATE INDEX IF NOT EXISTS idx_patents_status_type ON patents(legal_status, patent_type);
CREATE TABLE IF NOT EXISTS patent_documents (
  patent_id INTEGER PRIMARY KEY,
  object_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'application/pdf',
  size INTEGER NOT NULL DEFAULT 0,
  uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (patent_id) REFERENCES patents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS annual_targets (
  year INTEGER PRIMARY KEY,
  application_target INTEGER NOT NULL DEFAULT 0,
  grant_target INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  patent_id INTEGER NOT NULL,
  fee_type TEXT NOT NULL DEFAULT '年费',
  fee_region TEXT NOT NULL DEFAULT '',
  due_date TEXT NOT NULL,
  amount REAL,
  raw_value TEXT NOT NULL DEFAULT '',
  currency TEXT NOT NULL DEFAULT 'CNY',
  status TEXT NOT NULL DEFAULT '待缴费',
  paid_date TEXT,
  note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  source_key TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (patent_id) REFERENCES patents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fees_due_status ON fees(due_date,status);
CREATE TABLE IF NOT EXISTS notification_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  fee_id INTEGER NOT NULL,
  reminder_key TEXT NOT NULL,
  sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(fee_id, reminder_key),
  FOREIGN KEY (fee_id) REFERENCES fees(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notification_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status TEXT NOT NULL,
  reminder_count INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT ''
);
"""


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_database() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(patents)")}
        if "family_number" not in columns:
            conn.execute("ALTER TABLE patents ADD COLUMN family_number TEXT")
        if "family_name" not in columns:
            conn.execute("ALTER TABLE patents ADD COLUMN family_name TEXT")
        if "inventors" not in columns:
            conn.execute("ALTER TABLE patents ADD COLUMN inventors TEXT NOT NULL DEFAULT ''")
        migrations = {
            "country_region": "TEXT NOT NULL DEFAULT '中国'",
            "publication_date": "TEXT",
            "expiration_date": "TEXT",
            "agent_reference": "TEXT NOT NULL DEFAULT ''",
            "claim_count": "INTEGER",
            "independent_claim_count": "INTEGER",
            "page_count": "INTEGER",
            "cn_draft_status": "TEXT NOT NULL DEFAULT ''",
            "foreign_draft_status": "TEXT NOT NULL DEFAULT ''",
            "related_numbers": "TEXT NOT NULL DEFAULT ''",
            "case_notes": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in migrations.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE patents ADD COLUMN {name} {definition}")
        fee_columns = {row[1] for row in conn.execute("PRAGMA table_info(fees)")}
        fee_migrations = {
            "fee_region": "TEXT NOT NULL DEFAULT ''",
            "raw_value": "TEXT NOT NULL DEFAULT ''",
            "source": "TEXT NOT NULL DEFAULT 'manual'",
            "source_key": "TEXT",
        }
        for name, definition in fee_migrations.items():
            if name not in fee_columns:
                conn.execute(f"ALTER TABLE fees ADD COLUMN {name} {definition}")
        conn.execute("DROP INDEX IF EXISTS idx_fees_source_key")
        conn.execute("CREATE UNIQUE INDEX idx_fees_source_key ON fees(source_key)")
        conn.execute("""UPDATE patents SET country_region=CASE
            WHEN UPPER(REPLACE(application_number,' ','')) LIKE 'EP%' THEN '欧洲'
            WHEN UPPER(REPLACE(application_number,' ','')) LIKE 'IN%' THEN '印度'
            WHEN UPPER(REPLACE(application_number,' ','')) LIKE 'PCT%' OR UPPER(REPLACE(application_number,' ','')) LIKE 'WO%' THEN 'PCT'
            WHEN UPPER(REPLACE(application_number,' ','')) LIKE 'US%' OR application_number LIKE '%/%' THEN '美国'
            ELSE '中国' END
            WHERE country_region IS NULL OR TRIM(country_region)='' OR
            (country_region='中国' AND (UPPER(REPLACE(application_number,' ','')) LIKE 'US%' OR
             UPPER(REPLACE(application_number,' ','')) LIKE 'EP%' OR UPPER(REPLACE(application_number,' ','')) LIKE 'IN%' OR
             UPPER(REPLACE(application_number,' ','')) LIKE 'PCT%' OR UPPER(REPLACE(application_number,' ','')) LIKE 'WO%' OR application_number LIKE '%/%'))""")
        conn.execute("UPDATE patents SET family_number=application_number WHERE family_number IS NULL OR TRIM(family_number)='' ")
        conn.execute("UPDATE patents SET family_name=title WHERE family_name IS NULL OR TRIM(family_name)='' ")


def default_notification_config() -> dict:
    return {"enabled": False, "webhook": "", "scheduleTime": "17:30"}


def load_notification_config() -> dict:
    config = default_notification_config()
    if NOTIFICATION_CONFIG_PATH.exists():
        try:
            saved = json.loads(NOTIFICATION_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update(saved)
        except (OSError, ValueError):
            pass
    return config


def validate_wecom_webhook(value: str) -> str:
    webhook = clean_text(value)
    if not webhook:
        raise ValueError("请填写企业微信群机器人的 Webhook 地址。")
    parsed = urllib.parse.urlsplit(webhook)
    if parsed.scheme != "https" or parsed.hostname != "qyapi.weixin.qq.com":
        raise ValueError("Webhook 必须使用企业微信官方地址 qyapi.weixin.qq.com。")
    if parsed.path != "/cgi-bin/webhook/send" or not urllib.parse.parse_qs(parsed.query).get("key"):
        raise ValueError("Webhook 地址格式不正确，请从企业微信群机器人资料中完整复制。")
    return webhook


def normalized_notification_config(payload: dict) -> dict:
    current = load_notification_config()
    webhook = clean_text(payload.get("webhook")) or clean_text(current.get("webhook"))
    enabled = bool(payload.get("enabled"))
    schedule_time = clean_text(payload.get("scheduleTime"), "17:30")
    try:
        datetime.strptime(schedule_time, "%H:%M")
    except ValueError as exc:
        raise ValueError("每日推送时间格式不正确。") from exc
    if webhook:
        webhook = validate_wecom_webhook(webhook)
    if enabled and not webhook:
        raise ValueError("启用自动推送前，请先填写企业微信 Webhook。")
    return {"enabled": enabled, "webhook": webhook, "scheduleTime": schedule_time}


def save_notification_config(config: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = NOTIFICATION_CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(NOTIFICATION_CONFIG_PATH)


def scheduled_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}" --check-reminders'
    return f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}" --check-reminders'


def configure_notification_task(enabled: bool, schedule_time: str) -> dict:
    if os.name != "nt":
        return {"scheduled": False, "message": "自动定时任务仅支持 Windows。"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if enabled:
        command = ["schtasks.exe", "/Create", "/SC", "DAILY", "/TN", TASK_NAME,
                   "/TR", scheduled_command(), "/ST", schedule_time, "/F"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="mbcs",
                                errors="replace", creationflags=flags, timeout=20)
        if result.returncode:
            detail = clean_text(result.stderr or result.stdout, "Windows 拒绝创建定时任务。")
            raise ValueError(f"定时任务创建失败：{detail}")
        return {"scheduled": True, "message": f"每天 {schedule_time} 自动检查。"}
    subprocess.run(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True,
                   text=True, encoding="mbcs", errors="replace", creationflags=flags, timeout=20)
    return {"scheduled": False, "message": "自动推送已关闭。"}


def send_wecom_markdown(webhook: str, content: str) -> None:
    webhook = validate_wecom_webhook(webhook)
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}},
                      ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(webhook, data=body, method="POST",
                                     headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(request, timeout=12) as response:
        result = json.loads(response.read().decode("utf-8"))
    if int(result.get("errcode", -1)) != 0:
        raise ValueError(f"企业微信发送失败：{result.get('errmsg') or result.get('errcode')}")


def reminder_stage(days_left: int) -> tuple[str, str]:
    if days_left < 0:
        return "overdue", f"逾期 {-days_left} 天"
    if days_left == 0:
        return "due-0", "今天到期"
    if days_left <= 7:
        return "due-7", f"剩余 {days_left} 天"
    if days_left <= 30:
        return "due-30", f"剩余 {days_left} 天"
    return "due-90", f"剩余 {days_left} 天"


def record_notification_run(status: str, count: int, message: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO notification_runs(status,reminder_count,message) VALUES(?,?,?)",
                     (status, count, clean_text(message)[:500]))


def run_due_notifications() -> dict:
    config = load_notification_config()
    if not config.get("enabled"):
        raise ValueError("自动推送尚未启用。")
    webhook = validate_wecom_webhook(config.get("webhook", ""))
    today = datetime.now().date()
    horizon = (today + timedelta(days=90)).isoformat()
    with connect() as conn:
        rows = [dict(row) for row in conn.execute("""SELECT f.id, f.fee_type AS feeType,
        f.due_date AS dueDate, f.amount, f.currency, p.application_number AS applicationNumber,
        p.title AS patentTitle FROM fees f JOIN patents p ON p.id=f.patent_id
        WHERE f.status NOT IN ('已缴费','无需缴费','费用记录') AND f.due_date<>'' AND f.due_date<=?
        ORDER BY f.due_date,f.id""", (horizon,))]
        reminders = []
        for row in rows:
            try:
                days_left = (datetime.strptime(row["dueDate"], "%Y-%m-%d").date() - today).days
            except (TypeError, ValueError):
                continue
            key, label = reminder_stage(days_left)
            key = f"{row['dueDate']}:{key}"
            sent = conn.execute("SELECT 1 FROM notification_deliveries WHERE fee_id=? AND reminder_key=?",
                                (row["id"], key)).fetchone()
            if not sent:
                row.update({"reminderKey": key, "label": label, "daysLeft": days_left})
                reminders.append(row)
    if not reminders:
        record_notification_run("success", 0, "没有新的临期提醒。")
        return {"sent": 0, "message": "没有新的临期提醒。"}
    visible = reminders[:10]
    lines = ["## 专利缴费临期提醒", f"> 检查时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    for item in visible:
        color = "warning" if item["daysLeft"] >= 0 else "red"
        amount = "" if item["amount"] is None else f" · {item['currency']} {float(item['amount']):.2f}"
        lines.append(f"\n<font color=\"{color}\">{item['label']}</font> **{item['patentTitle']}**")
        lines.append(f"> {item['applicationNumber']} · {item['feeType']}{amount} · 截止 {item['dueDate']}")
    if len(reminders) > len(visible):
        lines.append(f"\n> 另有 {len(reminders) - len(visible)} 条，请打开本机看板查看。")
    try:
        send_wecom_markdown(webhook, "\n".join(lines))
        with connect() as conn:
            conn.executemany("INSERT OR IGNORE INTO notification_deliveries(fee_id,reminder_key) VALUES(?,?)",
                             [(row["id"], row["reminderKey"]) for row in reminders])
        record_notification_run("success", len(reminders), f"已推送 {len(reminders)} 条提醒。")
        return {"sent": len(reminders), "message": f"已推送 {len(reminders)} 条提醒。"}
    except Exception as exc:
        record_notification_run("failed", 0, str(exc))
        raise


def notification_status() -> dict:
    config = load_notification_config()
    webhook = clean_text(config.get("webhook"))
    with connect() as conn:
        last = conn.execute("SELECT run_at AS runAt,status,reminder_count AS reminderCount,message FROM notification_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"enabled": bool(config.get("enabled")), "scheduleTime": config.get("scheduleTime", "09:00"),
            "webhookConfigured": bool(webhook), "webhookMasked": ("••••••" + webhook[-6:]) if webhook else "",
            "lastRun": dict(last) if last else None}


def clean_text(value, default="") -> str:
    return str(value or default).strip()


def optional_int(value):
    text = clean_text(value)
    if not text:
        return None
    try:
        return max(0, int(float(text)))
    except (TypeError, ValueError):
        return None


def normalized_patent(payload: dict) -> dict:
    application_number = clean_text(payload.get("applicationNumber"))
    row = {
        "applicationNumber": application_number,
        "title": clean_text(payload.get("title")),
        "countryRegion": clean_text(payload.get("countryRegion")) or infer_country(application_number),
        "patentType": clean_text(payload.get("patentType"), "发明"),
        "applicationDate": clean_text(payload.get("applicationDate")),
        "publicationDate": clean_text(payload.get("publicationDate")),
        "publicationNumber": clean_text(payload.get("publicationNumber")),
        "grantDate": clean_text(payload.get("grantDate")),
        "expirationDate": clean_text(payload.get("expirationDate")),
        "legalStatus": clean_text(payload.get("legalStatus"), "审中"),
        "department": clean_text(payload.get("department"), "研发部"),
        "productLine": clean_text(payload.get("productLine"), "未归类"),
        "familyNumber": clean_text(payload.get("familyNumber")),
        "familyName": clean_text(payload.get("familyName")),
        "inventors": clean_text(payload.get("inventors")),
        "owner": clean_text(payload.get("owner"), "本公司"),
        "agentReference": clean_text(payload.get("agentReference")),
        "claimCount": optional_int(payload.get("claimCount")),
        "independentClaimCount": optional_int(payload.get("independentClaimCount")),
        "pageCount": optional_int(payload.get("pageCount")),
        "cnDraftStatus": clean_text(payload.get("cnDraftStatus")),
        "foreignDraftStatus": clean_text(payload.get("foreignDraftStatus")),
        "relatedNumbers": clean_text(payload.get("relatedNumbers")),
        "caseNotes": clean_text(payload.get("caseNotes")),
    }
    if not row["applicationNumber"] or not row["title"]:
        raise ValueError("申请号和专利名称不能为空。")
    if row["patentType"] not in {"发明", "实用新型", "外观设计"}:
        raise ValueError("专利类型不正确。")
    return row


def upsert_patent(conn: sqlite3.Connection, payload: dict, source="manual") -> int:
    row = normalized_patent(payload)
    conn.execute(
        """INSERT INTO patents
        (application_number,title,country_region,patent_type,application_date,publication_date,publication_number,grant_date,expiration_date,
         legal_status,department,product_line,family_number,family_name,inventors,owner,agent_reference,claim_count,
         independent_claim_count,page_count,cn_draft_status,foreign_draft_status,related_numbers,case_notes,source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(application_number) DO UPDATE SET
          title=excluded.title, country_region=excluded.country_region, patent_type=excluded.patent_type,
          application_date=excluded.application_date, publication_date=excluded.publication_date,
          publication_number=excluded.publication_number, grant_date=excluded.grant_date,
          expiration_date=excluded.expiration_date, legal_status=excluded.legal_status,
          department=excluded.department, product_line=excluded.product_line,
          family_number=excluded.family_number, family_name=excluded.family_name, inventors=excluded.inventors,
          owner=excluded.owner, agent_reference=excluded.agent_reference, claim_count=excluded.claim_count,
          independent_claim_count=excluded.independent_claim_count, page_count=excluded.page_count,
          cn_draft_status=excluded.cn_draft_status, foreign_draft_status=excluded.foreign_draft_status,
          related_numbers=excluded.related_numbers, case_notes=excluded.case_notes,
          source=excluded.source, updated_at=CURRENT_TIMESTAMP""",
        (row["applicationNumber"], row["title"], row["countryRegion"], row["patentType"], row["applicationDate"],
         row["publicationDate"] or None, row["publicationNumber"] or None,
         row["grantDate"] or None, row["expirationDate"] or None, row["legalStatus"],
         row["department"], row["productLine"], row["familyNumber"] or None,
         row["familyName"] or None, row["inventors"], row["owner"], row["agentReference"],
         row["claimCount"], row["independentClaimCount"], row["pageCount"], row["cnDraftStatus"],
         row["foreignDraftStatus"], row["relatedNumbers"], row["caseNotes"], source),
    )
    return int(conn.execute("SELECT id FROM patents WHERE application_number=?", (row["applicationNumber"],)).fetchone()[0])


def upsert_imported_fee(conn: sqlite3.Connection, patent_id: int, payload: dict) -> int:
    source_key = clean_text(payload.get("sourceKey"))
    if not source_key:
        raise ValueError("导入费用缺少来源编号。")
    amount = payload.get("amount")
    result = conn.execute(
        """INSERT INTO fees(patent_id,fee_type,fee_region,due_date,amount,raw_value,currency,status,paid_date,note,source,source_key)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_key) DO UPDATE SET patent_id=excluded.patent_id,fee_type=excluded.fee_type,
        fee_region=excluded.fee_region,due_date=excluded.due_date,amount=excluded.amount,raw_value=excluded.raw_value,
        currency=excluded.currency,status=excluded.status,paid_date=excluded.paid_date,note=excluded.note,
        source=excluded.source,updated_at=CURRENT_TIMESTAMP""",
        (patent_id, clean_text(payload.get("feeType"), "其他"), clean_text(payload.get("feeRegion")),
         clean_text(payload.get("dueDate")), amount, clean_text(payload.get("rawValue")),
         clean_text(payload.get("currency"), "CNY"), clean_text(payload.get("status"), "费用记录"),
         clean_text(payload.get("paidDate")) or None, clean_text(payload.get("note")), "excel", source_key),
    )
    return int(conn.execute("SELECT id FROM fees WHERE source_key=?", (source_key,)).fetchone()[0])


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def worksheet_xml(headers: list[str], rows: list[list], widths: list[int] | None = None) -> str:
    all_rows = [headers] + rows
    row_xml = []
    for row_number, row in enumerate(all_rows, 1):
        cells = []
        for col_number, value in enumerate(row, 1):
            ref = f"{column_name(col_number)}{row_number}"
            style = 1 if row_number == 1 else 2
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            else:
                text = html.escape("" if value is None else str(value))
                cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
        row_xml.append(f'<row r="{row_number}" ht="{28 if row_number == 1 else 54}" customHeight="1">{"".join(cells)}</row>')
    widths = widths or [18] * len(headers)
    cols = "".join(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>' for i, width in enumerate(widths, 1))
    end_ref = f"{column_name(len(headers))}{max(1, len(all_rows))}"
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<dimension ref="A1:{end_ref}"/><sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<sheetFormatPr defaultRowHeight="20"/><cols>{cols}</cols><sheetData>{"".join(row_xml)}</sheetData>
<autoFilter ref="A1:{column_name(len(headers))}{max(1, len(all_rows))}"/></worksheet>'''


def fee_display_value(row: dict) -> str:
    raw = clean_text(row.get("rawValue"))
    if raw:
        return raw
    amount = row.get("amount")
    if amount in (None, ""):
        return ""
    if isinstance(amount, float) and amount.is_integer():
        return str(int(amount))
    return str(amount)


def build_fee_summary_rows(patents: list[dict], fees: list[dict]) -> list[list]:
    patent_by_number = {clean_text(row.get("applicationNumber")): row for row in patents}
    grouped: dict[str, list[dict]] = {}
    for fee in fees:
        number = clean_text(fee.get("applicationNumber"))
        if number:
            grouped.setdefault(number, []).append(fee)
    numbers = list(patent_by_number)
    numbers.extend(number for number in grouped if number not in patent_by_number)
    result = []
    for number in numbers:
        patent = patent_by_number.get(number, {})
        items = grouped.get(number, [])
        summary = {key: "" for _, key in FEE_SUMMARY_COLUMNS}
        summary["applicationNumber"] = number
        summary["patentTitle"] = clean_text(patent.get("title")) or next(
            (clean_text(item.get("patentTitle")) for item in items if clean_text(item.get("patentTitle"))), "")
        region_lines = {key: [] for key in FEE_REGION_SUMMARY_KEYS.values()}
        for item in items:
            # A dated payment/reminder is represented by the dedicated "最近" columns below.
            # Keeping it in both places would create a duplicate when the exported file is re-imported.
            if clean_text(item.get("dueDate")):
                continue
            key = FEE_REGION_SUMMARY_KEYS.get(clean_text(item.get("feeRegion")))
            if not key:
                continue
            value = fee_display_value(item)
            currency = clean_text(item.get("currency"))
            suffix = f" {currency}" if currency and currency not in value.upper() else ""
            label = clean_text(item.get("feeType")) or "费用"
            region_lines[key].append(f"{label}={value}{suffix}" if value else label)
        for key, lines in region_lines.items():
            summary[key] = "\n".join(lines)
        open_items = [item for item in items if clean_text(item.get("dueDate")) and clean_text(item.get("status")) not in {"已缴费", "无需缴费"}]
        candidates = open_items or [item for item in items if clean_text(item.get("dueDate"))]
        if candidates:
            next_item = min(candidates, key=lambda item: clean_text(item.get("dueDate")) or "9999-12-31")
            summary.update({
                "nextFeeType": clean_text(next_item.get("feeType")),
                "nextDueDate": clean_text(next_item.get("dueDate")),
                "nextAmount": next_item.get("amount") if next_item.get("amount") is not None else "",
                "nextCurrency": clean_text(next_item.get("currency")),
                "nextStatus": clean_text(next_item.get("status")),
                "paidDate": clean_text(next_item.get("paidDate")),
                "note": clean_text(next_item.get("note")),
            })
        result.append([summary.get(key, "") for _, key in FEE_SUMMARY_COLUMNS])
    return result


def build_xlsx(patents: list[dict], fees: list[dict], template=False) -> bytes:
    patent_rows = [] if template else [[row.get(key, "") for _, key in PATENT_COLUMNS] for row in patents]
    fee_rows = [[
        "示例（导入时自动忽略）", "一种示例装置",
        "申请费=135 CNY\n实审费=2500 CNY",
        "申请费=1820 USD\n年费=800 USD",
        "申请费=1350 EUR", "申请费=1600 INR",
        "美国第3年年费", "2027-03-31", 800, "USD", "待缴费", "", "此行为填写示例",
    ]] if template else build_fee_summary_rows(patents, fees)
    notes = [
        ["填写说明"],
        ["专利台账工作表用于导入；申请号和专利名称为必填，其他字段可后续补充。"],
        ["日期请使用 yyyy-mm-dd；专利类型填写发明、实用新型或外观设计。"],
        ["相同专利族请填写相同专利族编号；多位发明人可用顿号或逗号分隔。权项和页数请填写数字。"],
        ["也支持直接导入既有的“专利家族”格式；其中合并单元格信息会自动向下继承。"],
        ["费用汇总按一个专利一行填写。中国、美国、欧洲、印度费用明细可写多项，每项一行，格式为：申请费=135 CNY。示例行导入时会自动忽略。"],
        ["最近待缴项目、最近缴费期限和缴费状态用于临期提醒；没有缴费期限的费用明细不会触发提醒。"],
    ]
    sheets = [
        ("专利台账", worksheet_xml([h for h, _ in PATENT_COLUMNS], patent_rows,
         [20,34,12,14,14,14,22,14,14,14,18,28,26,24,18,14,18,14,18,18,16,16,32,42])),
        ("费用汇总", worksheet_xml([h for h, _ in FEE_SUMMARY_COLUMNS], fee_rows,
         [20,34,28,28,28,28,24,16,16,10,14,14,32])),
    ]
    if template:
        sheets.append(("填写说明", worksheet_xml(["说明"], notes[1:], [85])))
    content_types = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>','<Default Extension="xml" ContentType="application/xml"/>','<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>','<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    content_types += [f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1)]
    workbook_sheets = "".join(f'<sheet name="{html.escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_) in enumerate(sheets,1))
    workbook_rels = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1)) + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="10"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="10"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF0D766E"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="2"><border/><border><bottom style="thin"><color rgb="FFDCE8E5"/></bottom></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs></styleSheet>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'+"".join(content_types)+"</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{workbook_sheets}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{workbook_rels}</Relationships>')
        z.writestr("xl/styles.xml", styles)
        for i,(_,xml) in enumerate(sheets,1): z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return buffer.getvalue()


def xlsx_matrices(data: bytes) -> dict[str, list[list]]:
    ns = {"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "p":"http://schemas.openxmlformats.org/package/2006/relationships"}
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.findall(".//m:t", ns))
                      for item in root.findall("m:si", ns)]
        date_styles = set()
        if "xl/styles.xml" in z.namelist():
            styles = ET.fromstring(z.read("xl/styles.xml"))
            custom_formats = {int(node.attrib["numFmtId"]): node.attrib.get("formatCode", "")
                              for node in styles.findall("m:numFmts/m:numFmt", ns)}
            cell_xfs = styles.find("m:cellXfs", ns)
            if cell_xfs is not None:
                for style_index, xf in enumerate(cell_xfs):
                    number_format = int(xf.attrib.get("numFmtId", "0"))
                    format_code = custom_formats.get(number_format, "").lower()
                    if number_format in set(range(14, 23)) | {45, 46, 47} or (
                            "y" in format_code and "d" in format_code):
                        date_styles.add(style_index)
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        relation = {node.attrib["Id"]: node.attrib["Target"] for node in rels.findall("p:Relationship", ns)}
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            target = relation[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
            sheet_path = target.lstrip("/") if target.startswith("/") else target if target.startswith("xl/") else "xl/" + target
            root = ET.fromstring(z.read(sheet_path)); matrix = []
            for xml_row in root.findall(".//m:sheetData/m:row", ns):
                values = {}
                for cell in xml_row.findall("m:c", ns):
                    ref = cell.attrib.get("r", ""); letters = "".join(c for c in ref if c.isalpha()); index = 0
                    for char in letters:
                        index = index * 26 + ord(char.upper()) - 64
                    cell_type = cell.attrib.get("t"); value = ""
                    if cell_type == "inlineStr":
                        value = "".join(node.text or "" for node in cell.findall(".//m:t", ns))
                    else:
                        node = cell.find("m:v", ns); raw = node.text if node is not None else ""
                        value = shared[int(raw)] if cell_type == "s" and raw else raw
                        style_index = int(cell.attrib.get("s", "0"))
                        if raw and cell_type != "s" and style_index in date_styles:
                            try:
                                value = (datetime(1899, 12, 30) + timedelta(days=float(raw))).strftime("%Y-%m-%d")
                            except ValueError:
                                pass
                    values[index - 1] = value
                if values:
                    matrix.append([values.get(i, "") for i in range(max(values) + 1)])
            result[sheet.attrib.get("name", "")] = matrix
    return result


def excel_date(value) -> str:
    text = clean_text(value)
    if not text or text == "/":
        return ""
    if text.replace(".", "", 1).isdigit():
        number = float(text)
        if 20000 <= number <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=number)).strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def split_lines(value) -> list[str]:
    return [part.strip() for part in clean_text(value).replace("\r", "\n").split("\n") if part.strip() and part.strip() != "/"]


def simple_identifier(value) -> str:
    text = clean_text(value)
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def infer_country(application_number: str) -> str:
    upper = application_number.upper().replace(" ", "")
    if upper.startswith("CN"): return "中国"
    if upper.startswith("EP"): return "欧洲"
    if upper.startswith("IN"): return "印度"
    if upper.startswith("PCT") or upper.startswith("WO"): return "PCT"
    if upper.startswith("US") or "/" in upper: return "美国"
    return "其他"


def looks_like_application_number(value: str) -> bool:
    compact = clean_text(value).upper().replace(" ", "")
    return bool(re.match(r"^(CN|US|EP|IN|PCT|WO)[A-Z0-9/.,-]*\d[A-Z0-9/.,-]*$", compact)
                or re.match(r"^\d{2}/+[\d,]+$", compact))


def normalize_patent_type(value, application_number="") -> str:
    text = clean_text(value)
    if "实用" in text or application_number.upper().rstrip().endswith("U"):
        return "实用新型"
    if "外观" in text or application_number.upper().rstrip().endswith("D"):
        return "外观设计"
    return "发明"


def normalize_legal_status(*values) -> str:
    text = " ".join(clean_text(value) for value in values if clean_text(value) and not excel_date(value))
    if any(word in text for word in ("获证", "授权", "核准")): return "已授权"
    if "复审" in text: return "复审"
    if "实审" in text: return "实审"
    if "OA" in text.upper(): return "OA"
    if "撤回" in text: return "撤回"
    if "驳回" in text: return "驳回"
    if "终止" in text: return "终止"
    if "未公开" in text: return "未公开"
    if "公开" in text: return "已公开"
    if any(word in text for word in ("递交", "申请")): return "已申请"
    return "状态待补"


def parse_claims(value) -> tuple[int | None, int | None]:
    parts = clean_text(value).replace("／", "/").split("/")
    total = optional_int(parts[0]) if parts else None
    independent = optional_int(parts[1]) if len(parts) > 1 else None
    return total, independent


def parse_standard_patents(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value).replace("／", "/") for value in matrix[0]]
    aliases = {name: key for name, key in PATENT_COLUMNS}
    aliases.update({"名称":"title", "发明人":"inventors", "设计人":"inventors",
                    "发明人/设计人":"inventors", "权利人":"owner", "申请人":"owner",
                    "申请人/权利人":"owner", "美国代理编号":"agentReference",
                    "US代理编号":"agentReference", "专利家族":"familyNumber",
                    "CN/US权项/独权":"claimSummary"})
    date_keys = {"applicationDate", "publicationDate", "grantDate", "expirationDate"}
    rows = []
    for values in matrix[1:]:
        row = {}
        for index, header in enumerate(headers):
            key = aliases.get(header)
            if not key: continue
            value = values[index] if index < len(values) else ""
            row[key] = excel_date(value) if key in date_keys else clean_text(value)
        if row.get("claimSummary"):
            row["claimCount"], row["independentClaimCount"] = parse_claims(row.pop("claimSummary"))
        if row.get("applicationNumber") or row.get("title"):
            rows.append(row)
    return rows


def parse_family_patents(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value).replace("\n", "").replace("／", "/") for value in matrix[0]]
    def col(name, default=-1):
        return headers.index(name) if name in headers else default
    positions = {name: col(name) for name in ("美国代理编号", "专利家族", "名称", "申请号", "申请日",
        "到期日/公开日/授权日", "发明人", "申请人", "CN/US权项/独权", "CN稿", "US稿",
        "CN状态/授权日", "CN到期日", "授权号/公开号", "EP", "IN", "CN")}
    value_at = lambda values, index: values[index] if 0 <= index < len(values) else ""
    rows = []; current_family = ""; current_family_name = ""; current_inventors = ""; current_owner = ""; current_agent = ""
    for values in matrix[1:]:
        raw_family = simple_identifier(value_at(values, positions["专利家族"]))
        raw_title = clean_text(value_at(values, positions["名称"]))
        if raw_family:
            current_family = raw_family
            current_family_name = raw_title or current_family_name
            current_inventors = ""; current_owner = ""; current_agent = ""
        elif raw_title and not current_family_name:
            current_family_name = raw_title
        inventor_value = clean_text(value_at(values, positions["发明人"]))
        owner_value = clean_text(value_at(values, positions["申请人"]))
        agent_value = clean_text(value_at(values, positions["美国代理编号"]))
        if inventor_value not in {"", "/"}: current_inventors = inventor_value
        if owner_value not in {"", "/"}: current_owner = owner_value
        if agent_value not in {"", "/"}: current_agent = agent_value
        number_lines = split_lines(value_at(values, positions["申请号"]))
        if not number_lines or not looks_like_application_number(number_lines[0]):
            continue
        application_number = number_lines[0]
        title = raw_title or current_family_name
        if not title: continue
        date_lines = split_lines(value_at(values, positions["申请日"]))
        dates = [excel_date(value) for value in date_lines]
        dates = [value for value in dates if value]
        claim_count, independent_count = parse_claims(value_at(values, positions["CN/US权项/独权"]))
        cn_status = clean_text(value_at(values, positions["CN稿"]))
        foreign_status = clean_text(value_at(values, positions["US稿"]))
        if cn_status == "/": cn_status = ""
        if foreign_status == "/": foreign_status = ""
        status_or_grant = value_at(values, positions["CN状态/授权日"])
        grant_date = excel_date(status_or_grant)
        publication_number = clean_text(value_at(values, positions["授权号/公开号"]))
        if not publication_number and len(number_lines) > 1:
            publication_number = "\n".join(number_lines[1:])
        related = []
        for heading in ("EP", "IN", "CN"):
            related_value = clean_text(value_at(values, positions[heading]))
            if related_value:
                related.append(f"{heading}: {related_value}")
        notes = []
        milestone = clean_text(value_at(values, positions["到期日/公开日/授权日"]))
        if milestone and milestone != "/": notes.append(f"原表重要日期：{milestone}")
        for index in (11, 12):
            extra = clean_text(value_at(values, index))
            if extra and extra != "/": notes.append(extra)
        rows.append({
            "applicationNumber": application_number, "title": title,
            "countryRegion": infer_country(application_number),
            "patentType": normalize_patent_type("", publication_number or application_number),
            "applicationDate": dates[0] if dates else "", "publicationDate": dates[1] if len(dates) > 1 else "",
            "publicationNumber": publication_number, "grantDate": grant_date,
            "expirationDate": excel_date(value_at(values, positions["CN到期日"])),
            "legalStatus": normalize_legal_status(cn_status, foreign_status, status_or_grant, "授权" if grant_date else ""),
            "familyNumber": current_family or application_number,
            "familyName": current_family_name or title, "inventors": current_inventors,
            "owner": current_owner, "agentReference": current_agent,
            "claimCount": claim_count, "independentClaimCount": independent_count,
            "cnDraftStatus": cn_status, "foreignDraftStatus": foreign_status,
            "relatedNumbers": "\n".join(related), "caseNotes": "\n".join(notes),
        })
    return rows


def enrich_from_fee_matrix(rows: list[dict], matrix: list[list]) -> None:
    if not matrix: return
    by_number = {clean_text(row.get("applicationNumber")): row for row in rows}
    headers = [clean_text(value).replace("\n", "") for value in matrix[0]]
    for values in matrix[1:]:
        application_lines = split_lines(values[3] if len(values) > 3 else "")
        if not application_lines or not looks_like_application_number(application_lines[0]): continue
        number = application_lines[0]
        row = by_number.get(number)
        if row is None:
            title = clean_text(values[2] if len(values) > 2 else "")
            if not title: continue
            dates = [excel_date(value) for value in split_lines(values[4] if len(values) > 4 else "")]
            row = {"applicationNumber": number, "title": title, "countryRegion": infer_country(number),
                   "patentType": normalize_patent_type(values[5] if len(values) > 5 else "", number),
                   "applicationDate": next((value for value in dates if value), ""),
                   "legalStatus": normalize_legal_status(values[6] if len(values) > 6 else ""),
                   "familyNumber": simple_identifier(values[1] if len(values) > 1 else "") or number,
                   "familyName": title}
            rows.append(row); by_number[number] = row
        row["pageCount"] = row.get("pageCount") or optional_int(values[7] if len(values) > 7 else "")
        row["claimCount"] = row.get("claimCount") or optional_int(values[8] if len(values) > 8 else "")
        related = split_lines(row.get("relatedNumbers"))
        for index, header in enumerate(headers):
            value = values[index] if index < len(values) else ""
            if header == "权利要求项" and optional_int(value) is not None:
                row["claimCount"] = optional_int(value)
            elif header == "独权项" and optional_int(value) is not None:
                row["independentClaimCount"] = optional_int(value)
            elif header in {"US编号", "EP编号", "IN编号"} and clean_text(value):
                prefix = header[:2]
                entry = f"{prefix}: {clean_text(value)}"
                if entry not in related: related.append(entry)
        if related: row["relatedNumbers"] = "\n".join(related)
        if len(values) > 5 and clean_text(values[5]): row["patentType"] = normalize_patent_type(values[5], number)
        if len(values) > 6 and clean_text(values[6]): row["legalStatus"] = normalize_legal_status(values[6], row.get("legalStatus"))
def numeric_amount(value):
    text = clean_text(value).replace(",", "")
    if not text or excel_date(text): return None
    try:
        return float(text)
    except ValueError:
        return None


def fee_currency(region: str, header: str) -> str:
    upper = header.upper()
    if "RMB" in upper or "CNY" in upper or "中代理" in header: return "CNY"
    if "USD" in upper: return "USD"
    if "EUR" in upper: return "EUR"
    return {"中国":"CNY", "美国":"USD", "欧洲":"EUR", "印度":"INR"}.get(region, "CNY")


def parse_legacy_fees(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value) for value in matrix[0]]
    sections = [(9, 32, "中国"), (36, 60, "美国"), (62, 77, "欧洲"), (79, 87, "印度")]
    fees = []
    for row_index, values in enumerate(matrix[1:], 2):
        number_lines = split_lines(values[3] if len(values) > 3 else "")
        if not number_lines or not looks_like_application_number(number_lines[0]): continue
        application_number = number_lines[0]
        for start, end, region in sections:
            previous_header = ""
            for index in range(start, min(end + 1, len(headers))):
                if headers[index]: previous_header = headers[index]
                raw = clean_text(values[index] if index < len(values) else "")
                if not raw or raw == "/": continue
                paid_date = excel_date(raw)
                if headers[index]:
                    header = headers[index]
                elif paid_date and previous_header:
                    header = previous_header + "（缴费日）"
                else:
                    header = f"原表 {column_name(index + 1)} 列（无标题）"
                amount = None if paid_date else numeric_amount(raw)
                fees.append({
                    "applicationNumber": application_number,
                    "feeRegion": region,
                    "feeType": header.replace("\n", " / "),
                    "dueDate": "", "amount": amount, "rawValue": raw,
                    "currency": fee_currency(region, header),
                    "status": "已缴费" if paid_date else "费用记录",
                    "paidDate": paid_date,
                    "note": f"原费用表 {column_name(index + 1)} 列导入",
                    "sourceKey": f"legacy:{application_number}:{row_index}:{column_name(index + 1)}",
                })
    return fees


def parse_standard_fees(matrix: list[list]) -> list[dict]:
    if not matrix: return []
    headers = [clean_text(value) for value in matrix[0]]
    mapping = {name: key for name, key in FEE_COLUMNS}
    rows = []
    for row_index, values in enumerate(matrix[1:], 2):
        row = {}
        for index, header in enumerate(headers):
            key = mapping.get(header)
            if not key: continue
            value = values[index] if index < len(values) else ""
            if key in {"dueDate", "paidDate"}: row[key] = excel_date(value)
            elif key == "amount": row[key] = numeric_amount(value)
            else: row[key] = clean_text(value)
        if row.get("applicationNumber") and row.get("feeType"):
            row["sourceKey"] = f"ledger:{row['applicationNumber']}:{row_index}"
            row.setdefault("status", "待缴费" if row.get("dueDate") else "费用记录")
            rows.append(row)
    return rows


def parse_fee_detail_entries(value, region: str) -> list[tuple[str, float | None, str, str]]:
    entries = []
    for entry in re.split(r"[\n；;]+", clean_text(value)):
        entry = entry.strip()
        if not entry:
            continue
        parts = re.split(r"[=＝:：]", entry, maxsplit=1)
        fee_type = clean_text(parts[0]) or "费用"
        raw = clean_text(parts[1]) if len(parts) > 1 else ""
        currency_match = re.search(r"\b(CNY|RMB|USD|EUR|INR)\b", raw, re.IGNORECASE)
        currency = (currency_match.group(1).upper() if currency_match else fee_currency(region, ""))
        if currency == "RMB":
            currency = "CNY"
        amount_text = re.sub(r"\b(CNY|RMB|USD|EUR|INR)\b", "", raw, flags=re.IGNORECASE).strip()
        entries.append((fee_type, numeric_amount(amount_text), raw, currency))
    return entries


def parse_summary_fees(matrix: list[list]) -> list[dict]:
    if not matrix:
        return []
    headers = [clean_text(value) for value in matrix[0]]
    indexes = {header: index for index, header in enumerate(headers) if header}
    rows = []
    region_columns = {"中国费用明细": "中国", "美国费用明细": "美国", "欧洲费用明细": "欧洲", "印度费用明细": "印度"}
    for row_index, values in enumerate(matrix[1:], 2):
        def value(name):
            index = indexes.get(name)
            return values[index] if index is not None and index < len(values) else ""
        number = clean_text(value("关联申请号"))
        if not number or number.startswith("示例"):
            continue
        title = clean_text(value("专利名称"))
        for column, region in region_columns.items():
            for item_index, (fee_type, amount, raw, currency) in enumerate(parse_fee_detail_entries(value(column), region), 1):
                rows.append({
                    "applicationNumber": number, "patentTitle": title, "feeRegion": region,
                    "feeType": fee_type, "dueDate": "", "amount": amount, "rawValue": raw,
                    "currency": currency, "status": "费用记录", "paidDate": "", "note": "",
                    "sourceKey": f"summary:{number}:{row_index}:{region}:{item_index}",
                })
        next_type = clean_text(value("最近待缴项目"))
        next_due = excel_date(value("最近缴费期限"))
        if next_type or next_due:
            currency = clean_text(value("币种")) or fee_currency(infer_country(number), "")
            raw_amount = clean_text(value("最近待缴金额"))
            rows.append({
                "applicationNumber": number, "patentTitle": title,
                "feeRegion": infer_country(number), "feeType": next_type or "待缴费用",
                "dueDate": next_due, "amount": numeric_amount(raw_amount), "rawValue": raw_amount,
                "currency": currency, "status": clean_text(value("缴费状态")) or ("待缴费" if next_due else "费用记录"),
                "paidDate": excel_date(value("实际缴费日")), "note": clean_text(value("备注")),
                "sourceKey": f"summarydue:{number}:{row_index}",
            })
    return rows


def parse_import_xlsx(data: bytes) -> tuple[list[dict], list[dict]]:
    sheets = xlsx_matrices(data)
    if "专利台账" in sheets:
        rows = parse_standard_patents(sheets["专利台账"])
    elif "专利家族" in sheets:
        rows = parse_family_patents(sheets["专利家族"])
    else:
        rows = parse_standard_patents(next(iter(sheets.values()), []))
    enrich_from_fee_matrix(rows, sheets.get("费用", []))
    unique = {}
    for row in rows:
        number = clean_text(row.get("applicationNumber"))
        if number: unique[number] = {**unique.get(number, {}), **row}
    fee_rows = parse_summary_fees(sheets.get("费用汇总", []))
    fee_rows.extend(parse_standard_fees(sheets.get("缴费台账", [])))
    if "费用" in sheets:
        fee_rows.extend(parse_legacy_fees(sheets["费用"]))
    return list(unique.values()), fee_rows


def parse_xlsx(data: bytes) -> list[dict]:
    return parse_import_xlsx(data)[0]


SELECT_SQL = """SELECT p.id, p.application_number AS applicationNumber, p.title,
p.country_region AS countryRegion, p.patent_type AS patentType, p.application_date AS applicationDate,
p.publication_date AS publicationDate, p.publication_number AS publicationNumber, p.grant_date AS grantDate,
p.expiration_date AS expirationDate,
p.legal_status AS legalStatus, p.department, p.product_line AS productLine,
p.family_number AS familyNumber, p.family_name AS familyName,
p.inventors, p.owner, p.agent_reference AS agentReference, p.claim_count AS claimCount,
p.independent_claim_count AS independentClaimCount, p.page_count AS pageCount,
p.cn_draft_status AS cnDraftStatus, p.foreign_draft_status AS foreignDraftStatus,
p.related_numbers AS relatedNumbers, p.case_notes AS caseNotes, p.source
FROM patents p"""


class PatentHandler(BaseHTTPRequestHandler):
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
        if length > 5 * 1024 * 1024:
            raise ValueError("请求内容过大。")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def route(self):
        return urllib.parse.urlsplit(self.path)

    def do_GET(self):
        route = self.route()
        if route.path == "/health":
            return self.json_response({"ok": True, "mode": "local", "database": str(DB_PATH.name)})
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
                    (DATA_DIR / object_path).unlink(missing_ok=True)
                except OSError:
                    pass
            self.json_response({"deleted": int(result.rowcount)})
        except ValueError as exc:
            self.json_response({"error": str(exc) or "编号无效。"}, 400)

    def send_static(self, request_path):
        name = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents and path != STATIC_DIR.resolve():
            return self.json_response({"error": "文件不存在。"}, 404)
        if not path.exists() or not path.is_file():
            path = STATIC_DIR / "index.html"
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


if __name__ == "__main__":
    main()

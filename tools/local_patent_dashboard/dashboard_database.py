"""SQLite schema, migrations and patent/fee persistence."""
from __future__ import annotations

import sqlite3
import dashboard_config as settings
from dashboard_validation import clean_text, normalized_patent


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
CREATE TABLE IF NOT EXISTS reading_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  publication_number TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reading_documents (
  patent_id INTEGER PRIMARY KEY REFERENCES reading_items(id) ON DELETE CASCADE,
  object_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  content_type TEXT NOT NULL DEFAULT 'application/pdf',
  size INTEGER NOT NULL DEFAULT 0,
  uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reader_fulltexts (
  item_key TEXT PRIMARY KEY,
  payload TEXT NOT NULL
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



def connect() -> sqlite3.Connection:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH, timeout=20)
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

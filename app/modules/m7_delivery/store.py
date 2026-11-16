import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from app.config import DATABASE_PATH
from app.modules.m3_localtime.resolve import utc_iso
from .contracts import DEFAULT_CONTRACT

DDL = (
    "CREATE TABLE IF NOT EXISTS delivery_active (id INTEGER PRIMARY KEY CHECK (id = 1), version TEXT NOT NULL, activated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS delivery_packages (delivery_no TEXT PRIMARY KEY, chain_id TEXT NOT NULL, contract_version TEXT NOT NULL, content BLOB NOT NULL, content_sha256 TEXT NOT NULL, status TEXT NOT NULL, missing TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, signed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS delivery_receipts (delivery_no TEXT PRIMARY KEY, receipt BLOB NOT NULL, receipt_sha256 TEXT NOT NULL, signed_at TEXT NOT NULL)",
)

def utc_now() -> str:
    return utc_iso(datetime.now(timezone.utc))

def _connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DATABASE_PATH, timeout=30)

def init_delivery_db() -> None:
    with _connect() as db:
        for statement in DDL:
            db.execute(statement)
        db.execute("INSERT OR IGNORE INTO delivery_active (id, version, activated_at) VALUES (1, ?, ?)", (DEFAULT_CONTRACT, utc_now()))

@contextmanager
def immediate():
    init_delivery_db()
    db = _connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def active_contract() -> str:
    init_delivery_db()
    with _connect() as db:
        return active_version_tx(db)

def active_version_tx(db: sqlite3.Connection) -> str:
    return db.execute("SELECT version FROM delivery_active WHERE id = 1").fetchone()[0]

def activate_contract(version: str) -> None:
    with immediate() as db:
        db.execute("UPDATE delivery_active SET version = ?, activated_at = ? WHERE id = 1", (version, utc_now()))

def _package_row(row: tuple) -> dict:
    return {"delivery_no": row[0], "chain_id": row[1], "contract_version": row[2], "content": row[3], "content_sha256": row[4], "status": row[5], "missing": json.loads(row[6]), "created_at": row[7], "signed_at": row[8]}

def _receipt_row(row: tuple) -> dict:
    return {"delivery_no": row[0], "receipt": row[1], "receipt_sha256": row[2], "signed_at": row[3]}

def get_package_tx(db: sqlite3.Connection, delivery_no: str) -> dict | None:
    row = db.execute("SELECT delivery_no, chain_id, contract_version, content, content_sha256, status, missing, created_at, signed_at FROM delivery_packages WHERE delivery_no = ?", (delivery_no,)).fetchone()
    return _package_row(row) if row else None

def get_package(delivery_no: str) -> dict | None:
    init_delivery_db()
    with _connect() as db:
        return get_package_tx(db, delivery_no)

def insert_package_tx(db: sqlite3.Connection, record: dict) -> None:
    db.execute("INSERT INTO delivery_packages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (record["delivery_no"], record["chain_id"], record["contract_version"], record["content"], record["content_sha256"], record["status"], json.dumps(record["missing"], ensure_ascii=False), record["created_at"], record["signed_at"]))

def mark_rejected_tx(db: sqlite3.Connection, delivery_no: str, issues: list[dict]) -> None:
    db.execute("UPDATE delivery_packages SET status = 'REJECTED', missing = ? WHERE delivery_no = ?", (json.dumps(issues, ensure_ascii=False), delivery_no))

def get_receipt_tx(db: sqlite3.Connection, delivery_no: str) -> dict | None:
    row = db.execute("SELECT delivery_no, receipt, receipt_sha256, signed_at FROM delivery_receipts WHERE delivery_no = ?", (delivery_no,)).fetchone()
    return _receipt_row(row) if row else None

def get_receipt(delivery_no: str) -> dict | None:
    init_delivery_db()
    with _connect() as db:
        return get_receipt_tx(db, delivery_no)

def record_receipt_tx(db: sqlite3.Connection, delivery_no: str, receipt: bytes, receipt_sha256: str, signed_at: str) -> None:
    db.execute("INSERT INTO delivery_receipts VALUES (?, ?, ?, ?)", (delivery_no, receipt, receipt_sha256, signed_at))
    db.execute("UPDATE delivery_packages SET status = 'SIGNED', signed_at = ? WHERE delivery_no = ?", (signed_at, delivery_no))

def package_count() -> int:
    init_delivery_db()
    with _connect() as db:
        return db.execute("SELECT COUNT(*) FROM delivery_packages").fetchone()[0]

def receipt_count() -> int:
    init_delivery_db()
    with _connect() as db:
        return db.execute("SELECT COUNT(*) FROM delivery_receipts").fetchone()[0]

"""Snapshot store: SQLite for state and history, plain files for each saved version."""
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("UHC_STORE_ROOT") or Path(__file__).resolve().parent.parent / "data" / "store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    policy_number TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_sha256 TEXT,
    pdf_path TEXT,
    extracted_path TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (source_id, policy_number, effective_date, content_hash)
);
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    baseline_version_id INTEGER REFERENCES versions(id),
    last_status TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    last_reason TEXT
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    version_id INTEGER,
    before_version_id INTEGER,
    reason TEXT
);
"""


def history_text(extracted):
    """Text of the sections excluded from comparison (the Policy History table)."""
    return "\n".join(re.sub(r"\s+", " ", p["text"]).strip()
                     for s in extracted["sections"] if s.get("excluded_from_diff") for p in s["paragraphs"])


def content_hash(extracted):
    """Hash of the diffable text only (History table excluded), whitespace-normalised.
    Re-saving the same policy as a new PDF file gives the same hash."""
    parts = []
    for s in extracted["sections"]:
        if s.get("excluded_from_diff"):
            continue
        parts.append([s["heading"],
                      [re.sub(r"\s+", " ", p["text"]).strip() for p in s["paragraphs"]],
                      [[r["code"], re.sub(r"\s+", " ", r["description"]).strip()] for r in s.get("code_table", [])]])
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)
        (self.root / "snapshots").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "monitor.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get_version(self, version_id):
        return self.db.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()

    def find_version(self, source_id, policy_number, effective_date, chash):
        return self.db.execute(
            "SELECT * FROM versions WHERE source_id=? AND policy_number=? AND effective_date=? AND content_hash=?",
            (source_id, policy_number, effective_date, chash)).fetchone()

    def get_state(self, source_id):
        return self.db.execute("SELECT * FROM source_state WHERE source_id = ?", (source_id,)).fetchone()

    def load_extracted(self, version_id):
        return json.loads(Path(self.get_version(version_id)["extracted_path"]).read_text())

    def save_version(self, source_id, extracted, pdf_bytes=None, raw_sha256=None, simulated=False):
        """Write the snapshot files, then the DB row. Returns the version id (existing id if already stored)."""
        chash = content_hash(extracted)
        existing = self.find_version(source_id, extracted["policy_number"], extracted["effective_date"], chash)
        if existing:
            return existing["id"]
        folder = self.root / "snapshots" / source_id
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{extracted['effective_date']}_{chash[:12]}" + ("_simulated" if simulated else "")
        pdf_path = None
        if pdf_bytes is not None:
            pdf_path = folder / f"{stem}.pdf"
            pdf_path.write_bytes(pdf_bytes)
        extracted_path = folder / f"{stem}.json"
        extracted_path.write_text(json.dumps(extracted, indent=2, ensure_ascii=False))
        with self.db:
            cur = self.db.execute(
                "INSERT INTO versions (source_id, policy_number, effective_date, content_hash, raw_sha256, pdf_path,"
                " extracted_path, simulated, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (source_id, extracted["policy_number"], extracted["effective_date"], chash, raw_sha256,
                 str(pdf_path) if pdf_path else None, str(extracted_path), int(simulated), _now()))
        return cur.lastrowid

    def record_check(self, source_id, status, reason=None, version_id=None, new_baseline_id=None, before_version_id=None):
        """Log a check. The baseline only moves when a good version is passed in as new_baseline_id."""
        now = _now()
        with self.db:
            self.db.execute("INSERT INTO checks (source_id, checked_at, status, version_id, before_version_id, reason)"
                            " VALUES (?,?,?,?,?,?)", (source_id, now, status, version_id, before_version_id, reason))
            state = self.get_state(source_id)
            baseline = new_baseline_id if new_baseline_id is not None else (state["baseline_version_id"] if state else None)
            self.db.execute(
                "INSERT INTO source_state (source_id, baseline_version_id, last_status, last_checked_at, last_reason)"
                " VALUES (?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET baseline_version_id=excluded.baseline_version_id,"
                " last_status=excluded.last_status, last_checked_at=excluded.last_checked_at, last_reason=excluded.last_reason",
                (source_id, baseline, status, now, reason))

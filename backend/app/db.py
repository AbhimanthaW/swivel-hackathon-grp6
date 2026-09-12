"""SQLite persistence: connection setup and schema. One file per process; safe
for the single-worker uvicorn process this hackathon backend runs under.
"""
import json
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "dispatch.sqlite3"

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")
_conn.execute("PRAGMA foreign_keys=ON")

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
  id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS problems (
  id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL,
  status TEXT NOT NULL,
  priority TEXT NOT NULL,
  crew_id TEXT,
  report_count INTEGER NOT NULL,
  first_reported_at TEXT,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_reports (
  id TEXT PRIMARY KEY,
  problem_id TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_reports_problem ON source_reports(problem_id);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

with _lock:
    _conn.executescript(SCHEMA)
    _conn.commit()


def conn():
    return _conn


def lock():
    return _lock


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


def loads(text):
    return json.loads(text)

"""Persistence layer: imports, published problems, source reports and the
idempotency cache. Each import/problem is stored as one JSON document plus a
few indexed columns used for filtering/sorting/pagination in SQL.
"""
import uuid
from datetime import datetime, timezone

from . import db


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- imports --

def create_import() -> dict:
    batch = {
        "id": new_id("imp"),
        "status": "draft",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "revision": 1,
        "files": [],
        "stages": [],
        "warnings": [],
        "error": None,
    }
    with db.lock():
        db.conn().execute(
            "INSERT INTO imports (id, revision, data, updated_at) VALUES (?, ?, ?, ?)",
            (batch["id"], batch["revision"], db.dumps(batch), batch["updatedAt"]),
        )
        db.conn().commit()
    return batch


def get_import(import_id: str):
    row = db.conn().execute("SELECT data FROM imports WHERE id = ?", (import_id,)).fetchone()
    return db.loads(row["data"]) if row else None


def list_imports(limit: int = 10):
    rows = db.conn().execute(
        "SELECT data FROM imports ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [db.loads(r["data"]) for r in rows]


def save_import(batch: dict):
    """Full upsert of an import document; bumps revision and updatedAt."""
    batch["revision"] = batch.get("revision", 0) + 1
    batch["updatedAt"] = now_iso()
    with db.lock():
        db.conn().execute(
            "UPDATE imports SET revision = ?, data = ?, updated_at = ? WHERE id = ?",
            (batch["revision"], db.dumps(batch), batch["updatedAt"], batch["id"]),
        )
        db.conn().commit()
    return batch


# --------------------------------------------------------------- problems --

def insert_problem(problem: dict):
    with db.lock():
        db.conn().execute(
            """INSERT INTO problems (id, revision, status, priority, crew_id, report_count, first_reported_at, data, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                problem["id"], problem["revision"], problem["status"], problem["priority"],
                (problem.get("assignment") or {}).get("crewId"), problem["reportCount"],
                problem["firstReportedAt"], db.dumps(problem), now_iso(),
            ),
        )
        db.conn().commit()


def get_problem(problem_id: str):
    row = db.conn().execute("SELECT data FROM problems WHERE id = ?", (problem_id,)).fetchone()
    return db.loads(row["data"]) if row else None


def save_problem(problem: dict, bump_revision=True):
    if bump_revision:
        problem["revision"] += 1
    with db.lock():
        db.conn().execute(
            """UPDATE problems SET revision = ?, status = ?, priority = ?, crew_id = ?, report_count = ?, data = ?, updated_at = ?
               WHERE id = ?""",
            (
                problem["revision"], problem["status"], problem["priority"],
                (problem.get("assignment") or {}).get("crewId"), problem["reportCount"],
                db.dumps(problem), now_iso(), problem["id"],
            ),
        )
        db.conn().commit()
    return problem


PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def list_problems(status="open", priority="", crew="", q="", sort="priority", page=1, page_size=15):
    rows = db.conn().execute("SELECT data FROM problems").fetchall()
    items = [db.loads(r["data"]) for r in rows]

    if status == "open":
        items = [p for p in items if p["status"] != "completed"]
    elif status != "all":
        items = [p for p in items if p["status"] == status]
    if priority:
        items = [p for p in items if p["priority"] == priority]
    if crew:
        items = [p for p in items if (p.get("assignment") or {}).get("crewId") == crew]
    if q:
        needle = q.strip().lower()
        if needle:
            items = [
                p for p in items
                if needle in p["title"].lower()
                or needle in (p.get("location") or {}).get("label", "").lower()
                or needle in p["workType"].lower()
            ]

    if sort == "reports":
        items.sort(key=lambda p: (-p["reportCount"], p["id"]))
    elif sort == "oldest":
        items.sort(key=lambda p: (p["firstReportedAt"] is None, p["firstReportedAt"] or "", p["id"]))
    else:
        items.sort(key=lambda p: (
            PRIORITY_RANK.get(p["priority"], 3),
            p["status"] == "completed",
            -p["reportCount"],
            p["firstReportedAt"] is None,
            p["firstReportedAt"] or "",
            p["id"],
        ))

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start:start + page_size]
    return page_items, total


def all_problems():
    rows = db.conn().execute("SELECT data FROM problems").fetchall()
    return [db.loads(r["data"]) for r in rows]


# ---------------------------------------------------------- source reports --

def insert_source_reports(problem_id: str, reports: list):
    with db.lock():
        db.conn().executemany(
            "INSERT INTO source_reports (id, problem_id, data) VALUES (?, ?, ?)",
            [(r["id"], problem_id, db.dumps(r)) for r in reports],
        )
        db.conn().commit()


def list_source_reports(problem_id: str, page: int = 1, page_size: int = 5):
    rows = db.conn().execute(
        "SELECT data FROM source_reports WHERE problem_id = ? ORDER BY id", (problem_id,)
    ).fetchall()
    items = [db.loads(r["data"]) for r in rows]
    total = len(items)
    start = (page - 1) * page_size
    return items[start:start + page_size], total


def known_report_references() -> set:
    """All source-report `reference` values (the original CSV report_id)
    already published from any prior import, so a later upload of the same
    reports.csv (in full or with a few new rows appended) only feeds the new
    rows into structuring and counts.
    """
    rows = db.conn().execute("SELECT data FROM source_reports").fetchall()
    refs = set()
    for r in rows:
        data = db.loads(r["data"])
        ref = data.get("reference")
        if ref is not None:
            refs.add(str(ref))
    return refs


# ---------------------------------------------------------------- idempotency --

def get_idempotent(key: str):
    row = db.conn().execute(
        "SELECT status_code, response FROM idempotency WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return None
    return row["status_code"], db.loads(row["response"])


def store_idempotent(key: str, method: str, path: str, status_code: int, response):
    with db.lock():
        db.conn().execute(
            "INSERT OR REPLACE INTO idempotency (key, method, path, status_code, response, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, method, path, status_code, db.dumps(response), now_iso()),
        )
        db.conn().commit()

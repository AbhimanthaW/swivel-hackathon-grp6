"""FastAPI implementation of contracts/openapi.json, served at /api/v1 on
port 8001 as expected by scripts/dev.mjs and docs/BACKEND-HANDOFF.md.
"""
import uuid
from fastapi import FastAPI, UploadFile, File, Form, Header, Query, Path as ApiPath, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import storage
from .config import CREWS, CREW_IDS, MAX_UPLOAD_BYTES
from .csv_ingest import parse_csv, CsvValidationError
from .pipeline import run_pipeline, upload_path

app = FastAPI(title="Muthuwella Works Dispatch API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FILE_ROLES = {"reports", "assets", "jobs_history"}
REQUEST_TIMEOUT_NOTE = None


def err(code, message, status_code=400, details=None, retryable=None):
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    if retryable is not None:
        error["retryable"] = retryable
    return JSONResponse(status_code=status_code, content={"error": error, "requestId": uuid.uuid4().hex})


def not_found(what="Record"):
    return err("NOT_FOUND", f"{what} not found.", 404)


def idempotent_replay(key: str):
    cached = storage.get_idempotent(key)
    if cached is None:
        return None
    status_code, response = cached
    return JSONResponse(status_code=status_code, content=response)


def idempotent_store(key: str, method: str, path: str, status_code: int, response: dict):
    storage.store_idempotent(key, method, path, status_code, response)


# --------------------------------------------------------------- overview --

@app.get("/api/v1/overview")
def get_overview():
    problems = storage.all_problems()
    open_problems = [p for p in problems if p["status"] != "completed"]
    counts = {"unreviewed": 0, "assigned": 0, "in_progress": 0, "completed": 0}
    for p in problems:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    crews_out = []
    for c in CREWS:
        assigned = sum(1 for p in problems if p["status"] == "assigned" and (p.get("assignment") or {}).get("crewId") == c["id"])
        in_progress = sum(1 for p in problems if p["status"] == "in_progress" and (p.get("assignment") or {}).get("crewId") == c["id"])
        crews_out.append({**c, "assignedCount": assigned, "inProgressCount": in_progress})

    imports = storage.list_imports(limit=25)
    completed_imports = [b for b in imports if b["status"] == "completed"]
    latest_completed = completed_imports[0]["id"] if completed_imports else None
    latest_any = imports[0] if imports else None
    summary = None
    if latest_any:
        publish_stage = next((s for s in latest_any["stages"] if s["key"] == "publish"), None)
        if publish_stage and publish_stage.get("message"):
            summary = publish_stage["message"]

    total_reports = sum(p["reportCount"] for p in problems)

    return {
        "sourceReportCount": total_reports,
        "distinctProblems": len(problems),
        "openProblems": len(open_problems),
        "highPriority": sum(1 for p in open_problems if p["priority"] == "high"),
        "counts": counts,
        "crews": crews_out,
        "analysisSummary": summary,
        "latestCompletedImportId": latest_completed,
    }


# --------------------------------------------------------------- problems --

@app.get("/api/v1/problems")
def list_problems(
    status: str = Query("open"),
    priority: str = Query(""),
    crew: str = Query(""),
    q: str = Query(""),
    sort: str = Query("priority"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(15, ge=1, le=50),
):
    valid_status = {"open", "all", "unreviewed", "assigned", "in_progress", "completed"}
    if status not in valid_status:
        return err("INVALID_STATUS", "Unknown status filter.", 400)
    if priority not in {"", "high", "medium", "low"}:
        return err("INVALID_PRIORITY", "Unknown priority filter.", 400)
    if sort not in {"priority", "reports", "oldest"}:
        return err("INVALID_SORT", "Unknown sort option.", 400)
    if len(q) > 200:
        return err("INVALID_QUERY", "Query too long.", 400)

    items, total = storage.list_problems(status, priority, crew, q, sort, page, pageSize)
    return {"items": items, "total": total, "page": page, "pageSize": pageSize}


@app.get("/api/v1/problems/{problem_id}")
def get_problem(problem_id: str = ApiPath(...)):
    problem = storage.get_problem(problem_id)
    if problem is None:
        return not_found("Problem")
    detail = {**problem, "assets": problem.get("assets", []), "recentJobs": problem.get("recentJobs", [])}
    return detail


@app.get("/api/v1/problems/{problem_id}/reports")
def list_problem_reports(problem_id: str, page: int = Query(1, ge=1)):
    problem = storage.get_problem(problem_id)
    if problem is None:
        return not_found("Problem")
    items, total = storage.list_source_reports(problem_id, page, page_size=5)
    return {"items": items, "total": total, "page": page, "pageSize": 5}


def _revision_conflict(problem, body_revision):
    return err(
        "REVISION_CONFLICT",
        f"This record has changed since you loaded it (current revision {problem['revision']}). Refresh before retrying.",
        409,
    )


@app.post("/api/v1/problems/{problem_id}/decision")
async def decision_problem(problem_id: str, request: Request, idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if not idempotency_key:
        return err("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required.", 400)
    replay = idempotent_replay(idempotency_key)
    if replay is not None:
        return replay
    body = await request.json()
    problem = storage.get_problem(problem_id)
    if problem is None:
        return not_found("Problem")
    if problem["status"] == "completed":
        return err("INVALID_STATE", "Priority cannot change on a completed problem.", 409)
    revision = body.get("revision")
    priority = body.get("priority")
    reason = (body.get("reason") or "").strip()
    if priority not in {"high", "medium", "low"}:
        return err("INVALID_PRIORITY", "priority must be high, medium or low.", 400)
    if revision != problem["revision"]:
        resp = _revision_conflict(problem, revision)
        return resp
    overridden = priority != problem["priorityRecommendation"]["level"]
    if overridden and not reason:
        return err("REASON_REQUIRED", "A reason is required when overriding the recommended priority.", 400,
                   details=[{"field": "reason", "message": "Required when changing the recommended level."}])
    problem["priority"] = priority
    problem["decision"] = {
        **(problem.get("decision") or {"crewOverridden": False, "crewReason": None}),
        "priorityOverridden": overridden,
        "priorityReason": reason or None,
        "reviewConfirmed": (problem.get("decision") or {}).get("reviewConfirmed", False),
        "by": "maya",
        "at": storage.now_iso(),
    }
    problem["decision"].setdefault("crewOverridden", False)
    problem["decision"].setdefault("crewReason", None)
    problem = storage.save_problem(problem)
    idempotent_store(idempotency_key, "POST", f"/problems/{problem_id}/decision", 200, problem)
    return problem


@app.post("/api/v1/problems/{problem_id}/assign")
async def assign_problem(problem_id: str, request: Request, idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if not idempotency_key:
        return err("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required.", 400)
    replay = idempotent_replay(idempotency_key)
    if replay is not None:
        return replay
    body = await request.json()
    problem = storage.get_problem(problem_id)
    if problem is None:
        return not_found("Problem")
    if problem["status"] not in {"unreviewed", "assigned"}:
        return err("INVALID_STATE", "Only unreviewed or already-assigned problems can be (re)assigned.", 409)
    revision = body.get("revision")
    crew_id = body.get("crewId")
    reason = (body.get("reason") or "").strip()
    review_confirmed = body.get("reviewConfirmed")
    location_override = (body.get("location") or "").strip()
    if crew_id not in CREW_IDS:
        return err("INVALID_CREW", "crewId must reference a configured crew.", 400)
    if review_confirmed is not True:
        return err("REVIEW_NOT_CONFIRMED", "reviewConfirmed must be true to dispatch work.", 400)
    if revision != problem["revision"]:
        return _revision_conflict(problem, revision)
    if not problem.get("location") and not location_override:
        return err("LOCATION_REQUIRED", "An actionable confirmed location is required for unresolved locations.", 400,
                   details=[{"field": "location", "message": "Required when the evidence location is unresolved."}])
    crew_overridden = crew_id != (problem["crewRecommendation"].get("crewId"))
    if crew_overridden and not reason:
        return err("REASON_REQUIRED", "A reason is required when choosing a different crew than recommended.", 400,
                   details=[{"field": "reason", "message": "Required when overriding the crew recommendation."}])
    if location_override:
        problem["location"] = {"label": location_override, "detail": problem.get("location", {}).get("label") if problem.get("location") else None}
    problem["status"] = "assigned"
    problem["assignment"] = {
        "id": storage.new_id("asg"),
        "crewId": crew_id,
        "assignedAt": storage.now_iso(),
        "startedAt": None,
        "completedAt": None,
    }
    problem["decision"] = {
        **(problem.get("decision") or {"priorityOverridden": False, "priorityReason": None}),
        "crewOverridden": crew_overridden,
        "crewReason": reason or None,
        "reviewConfirmed": True,
        "by": "maya",
        "at": storage.now_iso(),
    }
    problem["decision"].setdefault("priorityOverridden", False)
    problem["decision"].setdefault("priorityReason", None)
    problem = storage.save_problem(problem)
    idempotent_store(idempotency_key, "POST", f"/problems/{problem_id}/assign", 200, problem)
    return problem


@app.post("/api/v1/problems/{problem_id}/status")
async def status_problem(problem_id: str, request: Request, idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if not idempotency_key:
        return err("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required.", 400)
    replay = idempotent_replay(idempotency_key)
    if replay is not None:
        return replay
    body = await request.json()
    problem = storage.get_problem(problem_id)
    if problem is None:
        return not_found("Problem")
    revision = body.get("revision")
    crew_id = body.get("crewId")
    new_status = body.get("status")
    if new_status not in {"in_progress", "completed"}:
        return err("INVALID_STATUS", "status must be in_progress or completed.", 400)
    if not problem.get("assignment") or problem["assignment"]["crewId"] != crew_id:
        return err("CREW_MISMATCH", "This crew is not associated with the current assignment.", 403)
    allowed = {("assigned", "in_progress"), ("in_progress", "completed")}
    if (problem["status"], new_status) not in allowed:
        return err("INVALID_TRANSITION", f"Cannot move from {problem['status']} to {new_status}.", 409)
    if revision != problem["revision"]:
        return _revision_conflict(problem, revision)
    now = storage.now_iso()
    if new_status == "in_progress":
        problem["assignment"]["startedAt"] = now
    else:
        problem["assignment"]["completedAt"] = now
    problem["status"] = new_status
    problem = storage.save_problem(problem)
    idempotent_store(idempotency_key, "POST", f"/problems/{problem_id}/status", 200, problem)
    return problem


# ---------------------------------------------------------------- imports --

@app.get("/api/v1/imports")
def list_imports(limit: int = Query(10, ge=1, le=50)):
    items = storage.list_imports(limit)
    summaries = [{"id": b["id"], "status": b["status"], "createdAt": b["createdAt"], "updatedAt": b["updatedAt"]} for b in items]
    return {"items": summaries}


@app.post("/api/v1/imports")
async def create_import(idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if not idempotency_key:
        return err("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required.", 400)
    replay = idempotent_replay(idempotency_key)
    if replay is not None:
        return replay
    batch = storage.create_import()
    idempotent_store(idempotency_key, "POST", "/imports", 201, batch)
    return JSONResponse(status_code=201, content=batch)


@app.get("/api/v1/imports/{import_id}")
def get_import(import_id: str):
    batch = storage.get_import(import_id)
    if batch is None:
        return not_found("Import batch")
    return batch


@app.put("/api/v1/imports/{import_id}/files/{role}")
async def upload_import_file(
    import_id: str,
    role: str,
    file: UploadFile = File(...),
    revision: int = Form(...),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        return err("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required.", 400)
    replay = idempotent_replay(idempotency_key)
    if replay is not None:
        return replay
    if role not in FILE_ROLES:
        return err("INVALID_ROLE", "role must be reports, assets or jobs_history.", 400)
    batch = storage.get_import(import_id)
    if batch is None:
        return not_found("Import batch")
    if batch["status"] != "draft":
        return err("INVALID_STATE", "Files can only be uploaded while the batch is in draft.", 409)
    if revision != batch["revision"]:
        return _revision_conflict_batch(batch)

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        return err("UPLOAD_TOO_LARGE", f"File exceeds the {MAX_UPLOAD_BYTES} byte limit.", 413)
    if not (file.filename or "").lower().endswith(".csv"):
        return err("UNSUPPORTED_MEDIA_TYPE", "Only .csv files are accepted.", 415)

    file_entry = {
        "role": role, "name": file.filename or f"{role}.csv", "status": "validating",
        "sizeBytes": len(raw), "rowCount": None, "error": None,
    }
    try:
        _, row_count, warnings = parse_csv(role, raw)
        file_entry["status"] = "ready"
        file_entry["rowCount"] = row_count
        with open(upload_path(import_id, role), "wb") as fh:
            fh.write(raw)
        if warnings:
            batch["warnings"] = batch["warnings"] + [{"code": "CSV_WARNING", "message": w, "role": role} for w in warnings]
    except CsvValidationError as exc:
        file_entry["status"] = "invalid"
        file_entry["error"] = {"code": exc.code, "message": exc.message}

    batch["files"] = [f for f in batch["files"] if f["role"] != role] + [file_entry]
    batch = storage.save_import(batch)
    idempotent_store(idempotency_key, "PUT", f"/imports/{import_id}/files/{role}", 200, batch)
    return batch


def _revision_conflict_batch(batch):
    return err("REVISION_CONFLICT", f"This batch has changed (current revision {batch['revision']}). Refresh before retrying.", 409)


@app.post("/api/v1/imports/{import_id}/run")
async def run_import(import_id: str, request: Request, idempotency_key: str = Header(None, alias="Idempotency-Key")):
    if not idempotency_key:
        return err("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required.", 400)
    replay = idempotent_replay(idempotency_key)
    if replay is not None:
        return replay
    body = await request.json()
    batch = storage.get_import(import_id)
    if batch is None:
        return not_found("Import batch")
    if batch["status"] not in {"draft", "failed"}:
        return err("INVALID_STATE", "Analysis can only be run from draft or a failed batch.", 409)
    if body.get("revision") != batch["revision"]:
        return _revision_conflict_batch(batch)
    ready_roles = {f["role"] for f in batch["files"] if f["status"] == "ready"}
    if ready_roles != FILE_ROLES:
        return err("FILES_NOT_READY", "All three files (reports, assets, jobs_history) must be ready.", 422)

    batch["status"] = "queued"
    batch = storage.save_import(batch)
    run_pipeline(import_id)
    idempotent_store(idempotency_key, "POST", f"/imports/{import_id}/run", 202, batch)
    return JSONResponse(status_code=202, content=batch)

"""Background import pipeline: validate -> normalize -> structure (LLM) ->
consolidate -> enrich -> prioritize -> recommend crews -> publish.

Runs on a plain background thread per run. This is a single-process hackathon
backend; it is not designed for multi-worker deployment.
"""
import threading
import traceback
import pandas as pd

from . import storage, db
from .config import crew_id_for_work_type
from .csv_ingest import parse_csv, CsvValidationError
from .llm import structure_reports
from .db import UPLOAD_DIR

STAGE_DEFS = [
    ("validate_files", "Validate uploaded files"),
    ("normalize_sources", "Normalize sources"),
    ("structure_reports", "Structure reports with AI"),
    ("consolidate_problems", "Consolidate problems"),
    ("enrich_context", "Enrich with asset & job context"),
    ("prioritize", "Apply prioritization policy"),
    ("recommend_crews", "Recommend crews"),
    ("publish", "Publish results"),
]

PRIORITY_MAP = {"High": "high", "Medium": "medium", "Low": "low"}


def fresh_stages():
    return [{"key": k, "label": l, "status": "pending", "progress": None, "message": None} for k, l in STAGE_DEFS]


def upload_path(import_id: str, role: str) -> str:
    return str(UPLOAD_DIR / f"{import_id}__{role}.csv")


def _set_stage(batch, key, **fields):
    for stage in batch["stages"]:
        if stage["key"] == key:
            stage.update(fields)
            return
    batch["stages"].append({"key": key, "label": key, "status": "pending", "progress": None, "message": None, **fields})


def _find_file(batch, role):
    return next((f for f in batch["files"] if f["role"] == role), None)


UNKNOWN_LOCATION_TOKENS = {"unknown", "<unknown>", "n/a", "na", "none", "unspecified", ""}


def _clean_location(value):
    if not value:
        return None
    text = str(value).strip()
    return None if text.strip("<>").strip().lower() in UNKNOWN_LOCATION_TOKENS else text


def _coerce_datetime(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(ts):
        return None
    return ts.isoformat().replace("+00:00", "Z")


def _load_role(import_id, role):
    with open(upload_path(import_id, role), "rb") as fh:
        raw = fh.read()
    normalized, _, _ = parse_csv(role, raw)
    return normalized


def _asset_context_for(location_label, assets_df, limit=4):
    if not location_label or assets_df.empty:
        return []
    needle = location_label.lower()
    out = []
    for _, row in assets_df.iterrows():
        road = row.get("road_name")
        if road is None or pd.isna(road):
            continue
        road = str(road)
        if road.lower() in needle or needle in road.lower():
            out.append({
                "id": str(row.get("asset_id")) if pd.notna(row.get("asset_id")) else f"asset-{road}",
                "type": str(row.get("asset_type")) if pd.notna(row.get("asset_type")) else "road_segment",
                "roadClass": str(row.get("road_class")) if pd.notna(row.get("road_class")) else None,
                "ward": str(row.get("ward")) if pd.notna(row.get("ward")) else None,
                "facility": str(row.get("nearest_facility")) if pd.notna(row.get("nearest_facility")) else None,
                "distanceMeters": None,
                "relationship": f"Matches road '{road}' referenced by this problem's location.",
            })
        if len(out) >= limit:
            break
    return out


def _job_context_for(location_label, work_type, jobs_df, limit=4):
    if jobs_df.empty:
        return []
    needle = (location_label or "").lower()
    out = []
    for _, row in jobs_df.iterrows():
        loc = row.get("location")
        loc = str(loc) if pd.notna(loc) else ""
        if needle and loc and (loc.lower() in needle or needle in loc.lower()):
            relationship = "Same location as a prior recorded job; check for recurrence before dispatch."
        elif pd.notna(row.get("work_type")) and str(row.get("work_type")).strip().lower() == (work_type or "").lower():
            relationship = "Same work type recorded elsewhere; provided as general context only."
        else:
            continue
        out.append({
            "id": str(row.get("job_id")) if pd.notna(row.get("job_id")) else f"job-{len(out)}",
            "completedAt": _coerce_datetime(row.get("completed_at")),
            "crewName": str(row.get("crew_name")) if pd.notna(row.get("crew_name")) else "Unknown crew",
            "workType": str(row.get("work_type")) if pd.notna(row.get("work_type")) else "Unknown",
            "notes": str(row.get("notes")) if pd.notna(row.get("notes")) else None,
            "relationship": relationship,
        })
        if len(out) >= limit:
            break
    return out


def run_pipeline(import_id: str):
    thread = threading.Thread(target=_run, args=(import_id,), daemon=True)
    thread.start()


def _fail(batch, stage_key, message):
    _set_stage(batch, stage_key, status="failed", message=message)
    for stage in batch["stages"]:
        if stage["status"] == "pending":
            stage["status"] = "skipped"
    batch["status"] = "failed"
    batch["error"] = {"code": "PIPELINE_FAILED", "message": message, "retryable": True}
    storage.save_import(batch)


def _run(import_id: str):
    batch = storage.get_import(import_id)
    if batch is None:
        return
    batch["stages"] = fresh_stages()
    batch["status"] = "processing"
    batch["error"] = None
    batch = storage.save_import(batch)

    try:
        # -- validate_files ------------------------------------------------
        _set_stage(batch, "validate_files", status="running")
        batch = storage.save_import(batch)
        for role in ("reports", "assets", "jobs_history"):
            f = _find_file(batch, role)
            if not f or f["status"] != "ready":
                raise RuntimeError(f"File for role '{role}' is not ready.")
        _set_stage(batch, "validate_files", status="completed", message="All three files present and parsed.")
        batch = storage.save_import(batch)

        # -- normalize_sources ----------------------------------------------
        _set_stage(batch, "normalize_sources", status="running")
        batch = storage.save_import(batch)
        reports_df = _load_role(import_id, "reports")
        assets_df = _load_role(import_id, "assets")
        jobs_df = _load_role(import_id, "jobs_history")
        reports_df["received_at"] = reports_df["received_at"].apply(_coerce_datetime)
        jobs_df["completed_at"] = jobs_df["completed_at"].apply(_coerce_datetime)

        # Skip reports already published from an earlier import (same
        # report_id/reference), so re-uploading the same or an
        # appended-to reports.csv only feeds genuinely new rows into
        # structuring - duplicates never re-inflate reportCount.
        reports_df["report_id"] = reports_df["report_id"].astype(str)
        existing_refs = storage.known_report_references()
        duplicate_mask = reports_df["report_id"].isin(existing_refs)
        duplicate_count = int(duplicate_mask.sum())
        reports_df = reports_df[~duplicate_mask].reset_index(drop=True)

        message = f"{len(reports_df)} new report(s), {len(assets_df)} assets, {len(jobs_df)} job records normalized."
        if duplicate_count:
            message = (
                f"{len(reports_df)} new report(s), {len(assets_df)} assets, {len(jobs_df)} job records normalized "
                f"({duplicate_count} already-imported report(s) skipped as duplicates)."
            )
        _set_stage(batch, "normalize_sources", status="completed", message=message)
        if duplicate_count:
            batch["warnings"] = batch["warnings"] + [{
                "code": "DUPLICATE_REPORTS_SKIPPED",
                "message": f"{duplicate_count} report(s) matched a report already imported previously and were not reprocessed.",
                "role": "reports",
            }]
        batch = storage.save_import(batch)

        # -- structure_reports (LLM) -----------------------------------------
        _set_stage(batch, "structure_reports", status="running", progress=0)
        batch = storage.save_import(batch)

        def progress_cb(fraction):
            b = storage.get_import(import_id)
            _set_stage(b, "structure_reports", status="running", progress=fraction)
            storage.save_import(b)

        candidates = structure_reports(reports_df, assets_df, progress_cb=progress_cb)
        batch = storage.get_import(import_id)
        _set_stage(batch, "structure_reports", status="completed", progress=1,
                   message=f"AI proposed {len(candidates)} candidate problem groups.")
        batch = storage.save_import(batch)

        # -- consolidate_problems ---------------------------------------------
        _set_stage(batch, "consolidate_problems", status="running")
        batch = storage.save_import(batch)
        covered = {rid for c in candidates for rid in c["source_report_ids"]}
        all_ids = set(reports_df["report_id"])
        unaccounted = all_ids - covered
        warnings = list(batch["warnings"])
        if unaccounted:
            warnings.append({"code": "UNACCOUNTED_REPORTS", "message": f"{len(unaccounted)} report(s) could not be matched to a problem and were excluded.", "role": "reports"})
        batch["warnings"] = warnings
        _set_stage(batch, "consolidate_problems", status="completed",
                   message=f"{len(candidates)} problems consolidated from {len(covered)} accounted-for reports.")
        batch = storage.save_import(batch)

        # -- enrich_context -------------------------------------------------
        _set_stage(batch, "enrich_context", status="running")
        batch = storage.save_import(batch)
        reports_by_id = {str(r["report_id"]): r for _, r in reports_df.iterrows()}
        enriched = []
        for c in candidates:
            assets_ctx = _asset_context_for(c["location"], assets_df)
            jobs_ctx = _job_context_for(c["location"], c["recommended_crew"], jobs_df)
            enriched.append((c, assets_ctx, jobs_ctx))
        _set_stage(batch, "enrich_context", status="completed", message="Asset and job-history context attached where a location matched.")
        batch = storage.save_import(batch)

        # -- prioritize -------------------------------------------------------
        _set_stage(batch, "prioritize", status="running")
        batch = storage.save_import(batch)
        _set_stage(batch, "prioritize", status="completed", message="Priority levels and explanations assigned per problem.")
        batch = storage.save_import(batch)

        # -- recommend_crews ----------------------------------------------
        _set_stage(batch, "recommend_crews", status="running")
        batch = storage.save_import(batch)
        _set_stage(batch, "recommend_crews", status="completed", message="Crew recommendations matched against configured capabilities.")
        batch = storage.save_import(batch)

        # -- publish ------------------------------------------------------
        _set_stage(batch, "publish", status="running")
        batch = storage.save_import(batch)

        for candidate, assets_ctx, jobs_ctx in enriched:
            candidate["location"] = _clean_location(candidate.get("location"))
            source_ids = candidate["source_report_ids"]
            rows = [reports_by_id[str(rid)] for rid in source_ids if str(rid) in reports_by_id]
            received_dates = sorted(r["received_at"] for r in rows if r["received_at"])
            priority = PRIORITY_MAP.get(candidate["priority"], "medium")
            crew_id = crew_id_for_work_type(candidate["recommended_crew"])
            is_fallback = candidate["title"].startswith("Needs manual triage")
            needs_review = crew_id is None or is_fallback
            confidence = candidate.get("confidence")
            if isinstance(confidence, (int, float)):
                confidence = max(0.0, min(1.0, float(confidence)))
            else:
                confidence = None
            uncertainties = [candidate["priority_reason"]]
            if crew_id is None:
                uncertainties.append(f"No configured crew matches work type '{candidate['recommended_crew']}'.")
            if is_fallback:
                uncertainties.append("Automated triage could not confidently group this report; treat as unverified.")

            problem_id = storage.new_id("prob")
            problem = {
                "id": problem_id,
                "revision": 1,
                "title": candidate["title"],
                "description": " ".join(str(r.get("description") or "") for r in rows).strip() or candidate["title"],
                "location": {"label": candidate["location"], "detail": None} if candidate.get("location") else None,
                "workType": candidate["recommended_crew"],
                "firstReportedAt": received_dates[0] if received_dates else None,
                "latestReportedAt": received_dates[-1] if received_dates else None,
                "reportCount": candidate["evidence_count"],
                "priority": priority,
                "priorityRecommendation": {
                    "level": priority,
                    "explanation": candidate["priority_reason"],
                    "factors": [{"label": "AI triage", "detail": candidate["priority_reason"]}],
                },
                "crewRecommendation": {
                    "crewId": crew_id,
                    "reason": (
                        f"Work type '{candidate['recommended_crew']}' matches this crew's configured capability."
                        if crew_id else
                        f"No configured crew declares capability for work type '{candidate['recommended_crew']}'."
                    ),
                    "confidence": confidence,
                },
                "analysis": {
                    "state": "needs_review" if needs_review else "ready",
                    "method": "claude-structuring-v1",
                    "uncertainties": uncertainties,
                },
                "status": "unreviewed",
                "assignment": None,
                "decision": None,
                "assets": assets_ctx,
                "recentJobs": jobs_ctx,
            }
            storage.insert_problem(problem)

            source_reports = []
            for rid in source_ids:
                r = reports_by_id.get(str(rid))
                if r is None:
                    continue
                source_reports.append({
                    "id": storage.new_id("src"),
                    "reference": str(r["report_id"]),
                    "receivedAt": r["received_at"],
                    "channel": str(r.get("channel")) if pd.notna(r.get("channel")) else "unknown",
                    "description": str(r.get("description")) if pd.notna(r.get("description")) else "",
                    "location": str(r.get("location_text")) if pd.notna(r.get("location_text")) else None,
                })
            if source_reports:
                storage.insert_source_reports(problem_id, source_reports)

        batch = storage.get_import(import_id)
        publish_message = (
            f"{len(enriched)} problems published to the workspace."
            if enriched else
            "No new problems published - all uploaded reports were already imported previously."
        )
        _set_stage(batch, "publish", status="completed", message=publish_message)
        batch["status"] = "completed"
        storage.save_import(batch)

    except CsvValidationError as exc:
        batch = storage.get_import(import_id)
        _fail(batch, _current_running_stage(batch), f"File validation failed: {exc.message}")
    except Exception as exc:  # noqa: BLE001 - convert to a safe stored failure
        traceback.print_exc()
        batch = storage.get_import(import_id)
        _fail(batch, _current_running_stage(batch), f"Analysis failed: {exc}")


def _current_running_stage(batch):
    for stage in batch["stages"]:
        if stage["status"] == "running":
            return stage["key"]
    return batch["stages"][0]["key"] if batch["stages"] else "validate_files"

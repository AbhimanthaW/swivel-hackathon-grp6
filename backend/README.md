# Backend implementation

FastAPI implementation of `contracts/openapi.json`, served at `/api/v1` on
port 8001 (matching `scripts/dev.mjs`'s default `BACKEND_ORIGIN`).

## Run it

```
cd backend
pip install -r requirements.txt
python run.py
```

Requires an `ANTHROPIC_API_KEY` in the repo-root `.env` (see `.env.example`).
Then, from the repo root, `npm run dev` starts the frontend on 5173 and
proxies `/api/*` to this service.

## Layout

- `app/main.py` — HTTP routes for the contract in `contracts/openapi.json`.
- `app/pipeline.py` — the async import pipeline (`validate_files` →
  `normalize_sources` → `structure_reports` → `consolidate_problems` →
  `enrich_context` → `prioritize` → `recommend_crews` → `publish`), run on a
  background thread per `POST /imports/{id}/run`.
- `app/llm.py` — the Claude structuring step (candidate problems from
  redacted, normalized reports), adapted from the project's `problem_model.py`
  prototype.
- `app/csv_ingest.py` — flexible header-alias parsing/validation for the
  three CSV roles (`reports`, `assets`, `jobs_history`).
- `app/storage.py` / `app/db.py` — SQLite-backed persistence for import
  batches, published problems, source-report evidence and the
  idempotency-key cache.
- `app/config.py` — the crew roster (backend-owned, not derived from any
  export) and LLM model id.

## Reports upload frequently; assets/jobs_history don't

`reports` is expected to change on every run; `assets` and `jobs_history`
change rarely. Two mechanisms follow from that:

- **New-report dedup**: `normalize_sources` compares every uploaded row's
  `report_id` against every source-report reference already published from
  a prior import (`storage.known_report_references`) and only carries the
  unseen rows into structuring/publication - re-uploading the same or an
  appended-to `reports.csv` never re-counts or reprocesses old rows.
- **Carry-forward files**: uploading `assets` or `jobs_history` also saves
  it to a stable, batch-independent path and records it in the `latest_files`
  table (`storage.get_latest_file` / `set_latest_file`). `POST /imports`
  seeds every new draft batch's `files` with the last known-good `assets`/
  `jobs_history` entry (already `ready`), so a batch can be run after
  uploading only a fresh `reports.csv` - the coordinator can still replace
  either file in that batch if it actually changed. `reports` is never
  carried forward this way; it must be uploaded fresh each run.

## Known simplifications (hackathon scope)

- Single-process, single-worker: the import pipeline runs on a background
  thread in the same process, and SQLite access is serialized with a lock.
- Idempotency replay matches on key only (it does not hash-compare the
  retried payload against the original).
- Publishing is append-only per run; cross-batch reconciliation of existing
  problems/assignments (docs/BACKEND-HANDOFF.md §6) is not implemented.

# Ingestion & Normalization Layer

Turns varying council CSV exports into stable internal domain objects.
Nothing above this package needs to understand raw CSV schemas.

```
external CSV export
  -> file identification       validation.detect_source_type
  -> schema inspection         reader.read_table
  -> field mapping             mappings + validation.resolve_columns
  -> validation                validation
  -> normalization             normalizers
  -> canonical domain objects  models
  -> sanitized analysis input  sanitize        <-- the LLM boundary
```

## Layout

| File | Responsibility |
|---|---|
| `models.py` | Canonical Pydantic models, enums, `IngestionResult`, `DataQualityReport` |
| `mappings.py` | **The only place that knows external column names.** Field specs, aliases, enum alias tables |
| `normalizers.py` | Pure value-level primitives (text, dates, numbers, location hints, PII patterns) |
| `reader.py` | CSV decoding, delimiter sniffing, ragged rows, `RowView` canonical accessor |
| `validation.py` | Column resolution, source-type detection, coordinate/enum policy |
| `pipeline.py` | Shared per-file machinery: id minting, counters, issue collection, result assembly |
| `reports.py` / `assets.py` / `jobs.py` | One row -> one canonical record, per source |
| `sanitize.py` | PII redaction and `ReportAnalysisInput` — the boundary to the LLM stage |
| `service.py` | `IngestionService`, `IngestedBundle` — the public entry point |

## Usage

```python
from ingestion import IngestionService, SourceType

service = IngestionService()

# Primary API: the caller knows which upload slot the file came from.
result = service.ingest(file_bytes, SourceType.REPORTS, source_name="reports.csv")

print(result.accepted_count, result.rejected_count)
for issue in result.warnings:
    print(issue)

# All three uploads at once.
bundle = service.ingest_bundle(reports=..., assets=..., jobs=...)
bundle.summary()          # status payload for the processing UI
bundle.roads              # de-duplicated road directory
bundle.jobs_coverage      # measured date span of the jobs export
bundle.analysis_inputs()  # PII-safe input for the LLM stage
```

Accepts a `str`, `bytes`, `Path` or file object, so a FastAPI `UploadFile`
works directly.

## Canonical models

- **`ResidentReport`** — `ingest_id`, `source_report_id` (+`source_id_is_duplicate`),
  `channel`, `reported_at`, `description`, `location: RawLocation`,
  `reporter: ReporterContact` (all PII), `category_raw`, `urgency`, `status`,
  `photo_reference`, `source: SourceMetadata`.
- **`CouncilAsset`** — `asset_id`, `asset_type`, `road: RoadContext`, `location`,
  `owner`. `build_road_directory()` collapses these into **`RoadRecord`**, the
  de-duplicated road entity (the export repeats road attributes on every asset row).
- **`WorkJob`** — `job_id`, `completed_at`, `crew`/`crew_key`,
  `work_type`/`work_type_key`, `road_name`/`road_name_key`, `notes`.

Three rules the models follow:

1. **Nothing is manufactured.** A field the CSV does not contain is `None`.
2. **Uncertainty is preserved.** Unrecognised enum values keep a `*_raw`
   sibling; out-of-area coordinates are flagged, not deleted.
3. **`raw` is retained but never serialized.** Every record keeps its original
   row (`record.raw`) for debugging, excluded from every `model_dump()`.

### Internal vs source IDs

`ingest_id` is minted from `(source_type, source_id, row_number)` — deterministic
across re-uploads of the same file, and unique even when source IDs collide.

This matters: the sample export contains **two `report_id` values used on two
completely different reports**. Treating the source ID as a primary key would
silently destroy two resident reports. Colliding rows are all kept, flagged with
`source_id_is_duplicate=True` and listed in `quality.duplicate_source_ids`.

## Schema mapping strategy

Headers are normalized (`"Report ID"`, `"report_id"`, `"REPORT-ID"`, `"reportid"`
all match) and resolved against per-source alias tuples. Tolerated for free:
case, spacing, punctuation, separators, column reordering, extra columns,
missing optional columns, mixed delimiters, BOMs, quoted multi-line fields.

Each field declares a `Requirement`:

| | Meaning | Column absent | Value absent |
|---|---|---|---|
| `REQUIRED` | File is unusable without it | **error**, file rejected | warning, row kept |
| `SEMANTIC` | Domain needs it | warning, ingestion continues | warning, row kept |
| `OPTIONAL` | Enrichment | listed in `missing_optional_columns` | silent |

### Adding a new source schema or alias

1. Add the alias string to the relevant `FieldSpec.aliases` tuple in
   `mappings.py`. Order = preference when two columns compete.
2. For a new enum value, add it to the alias table in `mappings.py`
   (`CHANNEL_ALIASES`, `ASSET_TYPE_ALIASES`, ...). Unknown values are never
   dropped — they land in `*_raw` and `quality.unknown_values`.
3. For a wholly new source type: add a `SourceSchema`, a `parse_*_row`
   function and a `SourceParser`, then register it in `service._PARSERS`.

Aliases are asserted collision-free within a schema at import time, so
accidentally giving `"type"` to two fields fails immediately.

**Never** write `row["Some Exact CSV Header"]` outside `mappings.py`. Parsers
use `view["canonical_field"]`.

## File identification

`service.ingest(source, source_type)` — explicit, and what the API should use.

`service.identify()` / `ingest_auto()` score headers against all three schemas
(signature-field match 70%, header coverage 30%) and refuse to guess when
confidence is under 0.45 or two schemas are within 0.15 of each other.
**Filenames are never consulted.**

When the declared type disagrees with the columns, you get a
`SOURCE_TYPE_MISMATCH` issue naming both — so a user who drops assets.csv into
the reports slot sees one clear message instead of 184 rejected rows.

## Validation & error behaviour

A malformed row is rejected and reported; it never aborts the import.

- **`Severity.ERROR`** — the file (or a specific row) could not be used.
- **`Severity.WARNING`** — the row was kept, with a caveat.
- `result.ok` — the file was usable. `result.fatal` — nothing was ingested.

Every issue carries a stable `IssueCode`, plus `row_number` / `field` /
`source_id` where known, so the UI can group and link them.

`DataQualityReport` (per source) reports rows loaded/accepted/rejected, missing
IDs / descriptions / locations / timestamps, unparseable dates, duplicate source
IDs, invalid coordinates, unknown enum values, unused columns and missing
optional columns, plus source-specific `extras` (channel counts, road counts,
jobs coverage window, ...).

## PII sanitization boundary

```
CSV -> ResidentReport -> ReportAnalysisInput -> LLM
```

`sanitize.to_analysis_input()` is the **only** supported way to get resident
data into a prompt. It has no network dependency and no model coupling.

It does two things, because structural removal alone is not enough:

1. **Structural** — drops the whole `reporter` object (name, email, phone, raw
   contact) and `photo_reference`.
2. **Textual** — redacts email addresses, Sri Lankan phone numbers, NIC numbers,
   email sign-off blocks and mail footers from `description` and
   `location_text`, plus the known reporter name and its parts.

Step 2 is not optional: the real export contains sign-off blocks that repeat the
resident's name inside `description`.

`find_pii_leaks(payload, forbidden_values)` audits any payload for PII field
names, patterns or known literal values. Used in tests; usable as a runtime
assertion before sending a prompt.

## Location handling

Ingestion deliberately does **not** resolve locations.

It preserves `location.text` verbatim and adds `location.normalized_text` — a
lowercased, punctuation-collapsed hint with road-type abbreviations expanded
(`Temple Ln` -> `temple lane`). It does **not** correct spelling
(`Sation Passage` stays) and does **not** drop qualifiers
(`Kirula Road near the junction` != `Kirula Road`).

Later stages combine resident text, `RoadRecord.search_terms` (name + aliases)
and semantic analysis to decide whether two descriptions mean the same place.

## Scale

Single pass per file, streaming row-by-row, no repeated copies, no O(n²) work.
Measured ~15,000 rows/second, linear to 50k rows (500 reports/day arrives in
~0.03s). Plain Python — no queues, no distributed processing.

## Running the tests

```bash
cd backend
python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest
```

Tests use synthetic fixtures, not the council's sample export, so the parser is
verified independently of today's exact rows. One opt-in test in
`test_service.py` exercises the real dataset if it happens to be present locally
and skips otherwise.

Coverage: valid CSV, reordered columns, renamed columns via aliases, missing
optional columns, missing required columns, extra columns, duplicate headers,
malformed dates, empty descriptions/locations, duplicate IDs, missing IDs,
malformed/ragged/blank rows, multi-line quoted fields, alternative delimiters,
BOMs, unknown enum values, wrong source type in each direction, coordinate
sentinels and outliers, road-directory de-duplication, measured coverage window,
and PII sanitization (including the guard-the-guard test that `find_pii_leaks`
actually detects leaks).

## Integrating with the frontend/API contract

At the time of writing the repository is empty and no contract exists, so the
layer is isolated behind these models. When the contract lands:

1. Compare its expected report/problem fields against `ResidentReport`.
2. Add an explicit mapping module (e.g. `ingestion/contract.py`) rather than
   renaming canonical fields or introducing a second representation.
3. If there is a genuine mismatch, document *frontend expects X / ingestion
   produces Y / recommended change / why* before changing either side.

Likely friction points to check first:

- The frontend will show **problems** (deduplicated), not **reports**. This
  layer produces reports; grouping is the next stage's job.
- `ingest_id` vs `source_report_id` — the UI should key on `ingest_id`.
- `category_raw` is a council hint, not the dispatch work type.
- These models are already FastAPI-ready; `IngestionResult` can be returned
  from an upload endpoint directly.

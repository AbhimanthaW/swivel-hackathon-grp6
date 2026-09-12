# Backend — Muthuwella Works Dispatch

Python ingestion and normalization layer that sits underneath the API.

Takes the council's three CSV exports (resident reports, assets, jobs history)
and produces stable canonical domain objects, a per-source data-quality report,
and a PII-safe input for the LLM analysis stage.

## Setup

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

## Tests

```bash
./.venv/bin/python -m pytest
```

## Try it against a dataset

```bash
./.venv/bin/python -m ingestion.cli /path/to/Challenge03-Dataset
```

## Quick start

```python
from ingestion import IngestionService, SourceType

service = IngestionService()
bundle = service.ingest_bundle(reports=..., assets=..., jobs=...)

bundle.summary()          # upload/processing status for the UI
bundle.reports.records    # list[ResidentReport]
bundle.roads              # de-duplicated road directory
bundle.analysis_inputs()  # PII-safe input for the LLM stage
```

## Documentation

- [`docs/INGESTION.md`](docs/INGESTION.md) — architecture, models, schema
  mapping, validation behaviour, PII boundary, how to add a new source schema.
- [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) — decisions not specified by the
  brief, kept separate from the requirements.

## Scope

This layer does **not** deduplicate reports, prioritise problems, assign crews,
resolve locations or call a model. It provides clean, traceable inputs for those
stages:

```
CSV uploads -> normalized data -> AI analysis -> distinct problems -> dispatch UI
   ^^^^^^^^^^^^^^^^^^^^^^^^^^
   this package
```

No API key is read, stored or referenced anywhere in this layer.

# Muthuwella Works Dispatch

**Frontend scaffold and proposed backend contracts. No backend implementation or bundled data.**

The interface retains Maya's problem queue, explanations, evidence, priority and crew overrides, plus a lightweight crew worklist. An import workflow uploads the three CSVs separately and displays progress reported by the future Python ingestion/LLM service. Nothing parses CSV content or manufactures problems in the browser.

## Start here

```sh
git clone https://github.com/AbhimanthaW/swivel-hackathon-grp6.git
cd swivel-hackathon-grp6
npm start
```

Requires **Node.js 22+**. There are no npm dependencies to install. Open [the local frontend](http://127.0.0.1:5173).

Until your backend runs, **Backend not connected is the expected screen**. Upload/run controls require a real backend connection and a durable import batch. No empty API stubs, dummy problem cards, local database, source exports or runtime fixtures are included.

The dev server serves static files and proxies `/api/v1/*` to `http://127.0.0.1:8001`. It implements no domain endpoints. Customize the backend origin by copying `.env.example` to `.env` and editing it, then restart `npm start`. The Python server must preserve `/api/v1` in its routes. You do not need CORS for this same-origin local setup.

## Backend team: implementation order

1. Read [the handoff](docs/BACKEND-HANDOFF.md). Agree on the proposed contract before changing it.
2. Implement real persisted workspace reads and crew configuration. An actually empty database should return empty arrays and zero counts; an unavailable service should return an error.
3. Implement import creation and the three role-specific multipart uploads; normalize varying CSV columns inside Python.
4. Implement the asynchronous pipeline and persist its stage/status/errors. Validate model outputs against the LLM schema and source references.
5. Publish validated problems atomically; never overwrite Maya's decisions or crew progress on re-import.
6. Implement revision-checked, idempotent priority, assignment and crew-status mutations.
7. Run the [integration acceptance checks](docs/ACCEPTANCE.md) with real uploaded exports.

The `backend/` directory is a documentation-only placeholder for your team's implementation. Python framework, LLM provider, prompt strategy, database, ranking policy and hosting are deliberately not selected by this frontend scaffold.

## Contracts

- [OpenAPI 3.1](contracts/openapi.json): canonical HTTP shapes, parameters and responses. Import this file into your API tooling, or derive Python validation models from it.
- [Generated TypeScript declarations](frontend/domain.d.ts): derived from OpenAPI schemas. Run `npm run types` after changing the contract.
- [LLM structuring schema](contracts/llm-structuring.schema.json): backend-internal, untrusted candidate output. It cannot directly set job status, assignment or operational priority.
- [Pipeline and semantics](docs/BACKEND-HANDOFF.md): file roles, stage dependencies, stable identity, retry, privacy and publication rules.

## What is implemented

- Three upload slots: resident reports, assets/locations and recent jobs history; all required before analysis.
- Real multipart request wiring, file replacement while draft, backend validation feedback and retry feedback.
- Batch selection, asynchronous stage polling, failure display and explicit analysis retry.
- Paginated problem queue; lazy evidence; backend explanations and uncertainties.
- Priority/crew override forms, revision and idempotency headers, crew start/completion calls.
- Responsive desktop/mobile styling and backend-unavailable states.

**Not implemented:** ingestion, LLM calls, normalization/grouping/scoring, crew-policy configuration, persistence, authentication or any API response generator. Positive end-to-end integration is pending your backend. The coordinator/crew switch is a demo navigation control, not access control.

## Repository layout

```text
frontend/          UI modules, styles, API client and generated domain types
contracts/         OpenAPI and LLM output schema — no data examples
backend/           Backend ownership README only
scripts/           Static dev host/proxy and type generation
tests/            Transport and contract checks (no municipal datasets)
docs/              Backend handoff, acceptance checklist, current QA evidence
.github/workflows/ CI checks
```

## Checks

```sh
npm run check
npm run types
```

Checks cover JavaScript syntax, request encoding/errors, three-file readiness, schema references, LLM authority boundaries and generated-type drift. [QA.md](docs/QA.md) distinguishes frontend checks from backend integration that cannot yet run.

## Team workflow

Create feature branches and submit pull requests. Treat OpenAPI changes as API changes: update the schema, regenerate types, adjust the frontend and add backend acceptance coverage together. Never commit `.env`, uploaded CSVs, private exports, database files or LLM credentials. Git ignores common forms of these; the backend must store uploads outside tracked source directories.

No application data is persisted by this frontend. Selected `File` objects exist only for the upload interaction; import records, progress, published problems and decisions must be durable in the backend. Reload re-reads recent batches and workspace state from the service.

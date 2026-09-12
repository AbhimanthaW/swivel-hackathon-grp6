# Scaffold verification

This document covers the **data-free frontend scaffold**, not the earlier working prototype. The old prototype, database and CSV-derived records are not included in this repository or its history.

## Verified locally

- `npm run check`: JavaScript syntax checks pass; 10 Node tests pass; generated domain declarations match the OpenAPI schemas.
- Contract tests resolve every schema reference and check unique operation IDs, all-three-file readiness, model output authority boundaries, and absence of export-specific CSV fields/data files in frontend modules.
- Transport tests verify JSON errors, invalid responses, unknown write outcomes, multipart boundary handling, revision fields and idempotency headers. These tests intercept transport errors only; they do not populate the application with municipal fixtures.
- `npm start`: the static frontend/dev proxy runs on port 5173, with no dependency installation and no backend process bundled.
- Actual browser check with backend unavailable: three upload roles visible; upload/create actions disabled; no problem cards, fabricated counts or fake successful analysis.
- Coordinator/crew navigation and reconnect UI work in the browser while the backend is absent.
- Responsive check at 390×844: document width and scroll width both 390; no horizontal overflow. Three file controls and zero rendered problem rows confirmed. Temporary viewport override reset after testing.
- Proxy failure responses are real HTTP 503 errors; expected failed network requests should not be misrepresented as an application success.

## Not verified yet

Successful file uploads, backend stage progress, LLM structuring, result publication, real problem rendering from the new v1 API, decision persistence and multi-view crew completion cannot be verified until the team implements the backend. Existing UI wiring targets the documented contract but does not substitute for integration testing.

Complete `ACCEPTANCE.md` against the actual backend before claiming the hackathon golden path passes. No positive backend stub was added for these checks.

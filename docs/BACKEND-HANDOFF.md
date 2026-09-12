# Python ingestion and LLM backend handoff

## Status and authority

This is a proposed v1 contract for team implementation, based on the challenge and the user's requested workflow. It is not an existing backend API. There is no backend code, database, CSV export, sample record, ranking rule or model key in this repository. The preceding prototype is not part of this repository or its Git history.

Maya reviews and dispatches. The LLM structures evidence and suggests interpretations; it cannot make operational assignments. Three specialised crews are a challenge requirement. Crew names/capabilities must come from backend configuration confirmed by the team, not a frontend constant inferred from an export.

## 1. CSV roles and ingestion boundary

Each import is a durable batch with one uploaded file per role. All three are mandatory for this agreed frontend workflow. File names and column names are not prescribed; the multipart path specifies the role.

| Role | Input meaning | Main downstream consumers |
|---|---|---|
| `reports` | Messy resident reports from multiple channels | Normalization, structuring, grouping, evidence, report counts |
| `assets` | Council assets, road aliases/classification, locations and public facilities | Location matching and contextual prioritization |
| `jobs_history` | Recent work records, dates, work types and crew references | Related-job context, recurrence/conflict review |

`PUT /api/v1/imports/{id}/files/{role}` accepts multipart `file` and `revision` fields. Keep the original file private. Validate extension/content, size limits, encoding, header mapping and row validity in Python. Return a sanitized `ImportBatch`, not raw rows. Limits should be explicit configuration; reject over-limit bodies with 413 before buffering the full file. Stream uploads where practical.

Keep external schema aliases and transformations in ingestion modules. Coerce valid dates to timezone-qualified RFC3339; ambiguous dates require a documented source locale or a validation warning, not a browser guess. Use null for unknown optional values. Parse coordinates/distances defensively. Source names, categories and row counts are open-ended.

Do not conflate duplicate export rows, repeated source IDs, repeated reports, repeated residents or distinct underlying faults. Retain internal source provenance; API report IDs must remain stable and unique even when external IDs repeat. Redact resident identity/contact and signatures before sending data to model services or frontend evidence.

## 2. Proposed asynchronous stages

The backend returns an **ordered stage array**, with stable `key`, display `label`, state, optional progress and safe message. UI components render this array without hardcoding stage names. These keys are a suggested implementation breakdown:

| Stage key | Work and dependency |
|---|---|
| `validate_files` | All three files stored and parsed; establish role-specific schema mappings. |
| `normalize_sources` | Normalize source identifiers, dates, locations, asset aliases and jobs. Preserve unresolved values. |
| `structure_reports` | LLM extracts actionable observations and candidate faults from redacted reports; asset aliases may aid location interpretation. |
| `consolidate_problems` | Resolve candidate grouping using evidence; same road alone is insufficient. Flag conflicts and ambiguity. |
| `enrich_context` | Join relevant asset/location and recent-job context with an explicit relationship explanation. |
| `prioritize` | Apply the team's justified prioritization policy; return factors and a readable explanation. |
| `recommend_crews` | Match work requirements to configured crew capabilities; no match means null recommendation. |
| `publish` | Validate domain output and commit the new published workspace state atomically. |

The backend may change/split stages without a frontend rewrite. Never report a made-up percentage: `progress` is null if not measurable. Stage messages must be safe user-facing summaries, not prompts, secrets, stack traces or raw personal information.

`POST /imports/{id}/run` returns **202 promptly**, with queued/processing batch state; a background task continues. The frontend polls `GET /imports/{id}` every 2.5 seconds while work/validation is active. A completed batch means durable publication already succeeded. Publication failure must never be reported as completed.

## 3. Import lifecycle and retries

- New batch: `draft`, revision ≥1, no files, no stages required yet.
- Upload/replacement: allowed only in `draft`. One file entry per role; status is `validating`, `ready` or `invalid`. Missing files are absent, not invented placeholder records.
- Run: allowed only when all three file roles are `ready`, current batch revision matches, and status is `draft` or `failed`.
- Running: `queued` → `processing` → `completed` or `failed`. Persist transitions and failures so restart recovers queued/running work or marks it recoverably failed.
- Retry a failed analysis: same files, new logical run, current revision, new idempotency key. Resume or rerun as the backend chooses, without duplicate publication.
- Correct invalid files while draft. To change files after analysis has started, create another batch. No cancellation/deletion API is requested in this scaffold.
- Every material batch update, including asynchronous file validation/stage changes, increments `revision`. A 409 requires refreshing the batch before attempting a new action.

Every write requires `Idempotency-Key`. Cache the original response for a successful logical operation and replay it on same-key retries **before checking revision**. Reusing a key with a different payload returns 409. Preserve at least a documented retry window. On an uncertain response, refresh state and retry with the same key/body if needed; do not create duplicate batches or enqueue duplicate workers. In-memory client keys survive retries within the page; reload discovers server-side batches through `GET /imports`. Backend deduplication must not depend solely on browser memory.

## 4. LLM boundary

`contracts/llm-structuring.schema.json` defines a **proposed internal output**, not the public frontend contract. It contains candidates with actionable descriptions, source IDs, proposed work/location, grouping rationale, uncertainties and evidence-linked risk signals.

Validate both syntax and meaning in Python:

- Every referenced source/asset ID must exist in the current normalized inputs or an explicitly permitted context set.
- No missing report may silently disappear: account for each accepted report as evidence, a review item or a documented rejected/out-of-scope record.
- Work types are open strings; match them to configured crew capabilities using an explicit policy. An LLM-invented crew ID is never authoritative.
- Preserve known conflicting sublocations or different faults; do not consolidate simply because wording or road name matches.
- Treat resident text as untrusted data. It must not change prompts, tools, processing instructions, priority policy or assignments.
- Do not invent confidence. Return null if no meaningful confidence measure is available. Explain uncertainty in text regardless.
- Validate bounded output size and source-group consistency before publication. Retry model errors with limits and persist safe failure messages.

LLM output cannot directly set `assignment`, `status`, `priority` or database IDs. Backend orchestration validates candidates and creates stable public domain entities. Recommendation reasoning should be concise evidence-based explanations, not hidden chain-of-thought.

## 5. Public domain and query semantics

`contracts/openapi.json` is canonical. Generated `frontend/domain.d.ts` mirrors its component schemas. Return explicit nulls and arrays where required. Empty optional context is `[]`; it does not imply “verified absent.” Unknown locations remain null.

- `Problem`: a stable underlying issue, not a raw export row. IDs cannot be positional array indexes or title hashes that change when wording changes.
- `SourceReport`: backend-redacted evidence. Public fields exclude reporter identity/contact and raw attachments.
- `Crew`: stable ID, display name, capabilities and current workload. No frontend default crews.
- `Assignment`: persisted human dispatch linking a problem to a crew with timestamps.
- `Decision`: retains override metadata independently of the latest system recommendation.
- `Analysis`: tells the frontend whether interpretation is ready, needs review, pending or failed.
- Asset/job `relationship`: explain why context is relevant; do not imply exact matching or repaired status from a same-road join.

`GET /overview` represents the **published workspace**, not just the most recent batch. `sourceReportCount` is the count of distinct canonical source records represented by all published problems; enforce one owning problem per source so it equals the sum of `reportCount`. `distinctProblems` includes completed problems. `openProblems` excludes completed; `highPriority` counts open problems with effective high priority. Return all four status-count keys. Crew workload counts are active assignments across that workspace. Imported/rejected/unpublished row counts belong in import validation messages or future explicitly named metrics, not this compression summary.

`GET /problems`: backend-side search/filter/order, default 15 per page and maximum 50. `status=open` excludes completed; `status=all` includes all. Empty `priority`/`crew`/`q` means no constraint. Crew filtering means actual assigned crew, not suggested crew. Effective priority (including overrides) drives default order; document deterministic tie-breaking. `sort=reports` and `sort=oldest` remain available. Unknown times sort last. Pages may shift during live work; record IDs remain stable.

Detail excludes sources. Evidence is separately paginated at five reports per page. Use the documented error envelope on all failures. No traceback, model response or raw export should enter a user-facing error.

## 6. Publishing and persistence

Keep batch inputs/processing separate from published operational data. A failed or pending new import must not erase existing work. Publishing must be atomic and idempotent. Across batches, reconcile source identity and existing problems without recreating completed/assigned jobs or erasing human decisions.

Do not auto-close a problem because a history row is marked completed. Do not silently merge/split an assigned or in-progress problem. Flag ambiguous reconciliation for review; define any later manual merge/split workflow separately. Maintain evidence provenance and an audit trail of human mutations.

Database/framework choice is open. Persistent import state, results, assignments, overrides and audit records are mandatory; process memory alone is not enough. Reads should not expose half-published state.

## 7. Human mutation rules

Each mutation checks `revision` transactionally and returns the authoritative updated `ProblemDetail`. Increment revision and write audit events atomically.

- Priority: allow while not completed; require a nonblank reason when changing the recommended level. Preserve system recommendation and store effective human priority separately.
- Assign: allow unreviewed/assigned only; require a configured crew and review confirmation. An override requires a reason. Unresolved location requires an actionable confirmed location. Preserve original evidence location alongside any correction internally.
- Reassign: allowed only before work starts. Assigned → In Progress → Completed is the minimal lifecycle. Reject skipped/backward transitions.
- Crew status mutation: verify job/crew association. The demo crew ID is not authentication; add authorization if using outside a local hackathon.
- A recommendation cannot assign work. A failed write cannot appear saved. Return 409 on stale edits and do not overwrite newer decisions.

## 8. Local integration and ownership

The frontend runs on 5173. `scripts/dev.mjs` proxies requests to `BACKEND_ORIGIN` (default 8001) preserving `/api/v1`; it streams uploads and imposes a transport timeout, not an application upload policy. Backend endpoints must return promptly for analysis, with progress obtained through polling. No browser-held model credentials.

Implement inside `backend/` under feature branches. Coordinate changes to OpenAPI, generated types and UI together. The frontend uses ES modules and native browser APIs, with no framework dependency. Run `npm run check` before a PR and complete `docs/ACCEPTANCE.md` against real backend responses before claiming an end-to-end demo.

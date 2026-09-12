# Integration acceptance — pending backend implementation

These are acceptance criteria, not reported passes. Use real uploaded exports and a real persisted backend. Avoid adding mock records to the frontend to satisfy them.

## Import and processing

- [ ] Start with an actually empty backend: zero published counts, empty queue, backend-configured crews.
- [ ] Create a durable draft; upload reports, assets and jobs history separately under their roles, regardless of file names.
- [ ] Missing file, invalid CSV, ambiguous date/header mapping and over-limit upload have clear backend validation messages.
- [ ] Replacing a draft file replaces that role only and does not duplicate its records.
- [ ] Run stays unavailable until all three files are ready; backend also enforces this.
- [ ] Run returns promptly; persisted stage updates appear without fabricated progress.
- [ ] Model output schema violations, hallucinated references and prompt-injection text fail safely or become review items.
- [ ] Failed processing publishes no partial problems; previous published work remains intact.
- [ ] Retry, repeated network requests and restart do not duplicate runs, batches or publication.
- [ ] All accepted source reports are accounted for; public source IDs are unique despite repeated export IDs.

## Problems and evidence

- [ ] Different descriptions of the same fault consolidate; different sites/faults on one road do not collapse silently.
- [ ] Titles and descriptions are actionable. Priority and crew reasoning trace to evidence.
- [ ] Unknown location, unsupported work, missing context and uncertain grouping remain visible without invented values.
- [ ] Summary counts use the whole published workspace; filtering/pagination does not redefine totals.
- [ ] Large lists paginate server-side; source reports load separately in pages of five.
- [ ] Resident identity/contact and arbitrary personal details are redacted before evidence reaches the frontend.

## Human operations

- [ ] Maya changes priority with a reason and the queue reorders after the confirmed save.
- [ ] Maya accepts/overrides a crew; the saved decision is visually separate from the suggestion.
- [ ] Confirmed location is required when unresolved; wrong/unknown crew IDs and stale revisions are rejected.
- [ ] Assigned work appears in the crew view; Start and Complete persist and update another coordinator tab.
- [ ] Reload and backend restart retain decisions, assignment, completion and import state.
- [ ] Re-import never erases human overrides or creates duplicate active jobs.
- [ ] Interrupted writes are recoverable with revision/idempotency semantics; no success toast appears before confirmation.

## UI and failure recovery

- [ ] Real pending, failed, empty and unavailable responses render correctly.
- [ ] Long descriptions, additional work types and changed crew configuration need no component edits.
- [ ] Narrow crew view, keyboard dialog controls and evidence pagination remain usable.
- [ ] A complete live demo proves: three uploads → processing → consolidated problems → reasoning → human dispatch → crew completion → restart persistence.

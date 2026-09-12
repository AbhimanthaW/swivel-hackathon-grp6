# Ingestion Assumptions

Decisions the challenge brief does **not** specify, made to get the ingestion
layer working. Each is cheap to change, and where relevant the code point is
named. Kept separate from the requirements so reviewers can challenge them.

Questions worth putting to the product owner are marked **[ASK]**.

## Dates

1. **Slash dates are day-first.** `08/09/2026 09:28` is read as 8 September.
   Justified because the same export writes ISO `2026-09-08T...` for 200 of 240
   rows and all 40 slash rows fall on the same day. A US-format export would be
   silently misread. `normalizers.DATETIME_FORMATS`.
2. **Timestamps are naive local council time.** No timezone appears in the
   export. ISO strings with offsets are still parsed if they turn up.
3. **The jobs file's "previous two weeks" is data, not a rule.** The window is
   measured (`coverage_window()`, 15 days in the current export). Nothing
   hardcodes 14.

## Identifiers

4. **Source IDs are not primary keys.** The sample export reuses `MR-719106`
   and `MR-528208` on unrelated reports. All rows are kept under distinct minted
   `ingest_id`s and flagged. **[ASK]** is this an export bug or expected?
5. **`ingest_id` is deterministic** from `(source_type, source_id, row_number)`,
   so re-uploading the same file is idempotent. It is *not* stable if rows are
   reordered between exports — acceptable for a per-morning batch workflow, but
   it would need revisiting for incremental ingestion. **[ASK]** will Maya ever
   re-upload a partially overlapping export?

## Location

6. **Ingestion does not resolve locations.** Original text is preserved; only a
   normalized hint is added. Abbreviations are expanded (`Ln`->`lane`); spelling
   is *not* corrected (`Baeline extension`, `Sation Passage`, `Torington Avenue`
   stay as written) and qualifiers are *not* dropped.
7. **Commas do not separate road aliases.** Only `;`, `|` and `/` do, because
   alias values legitimately contain commas.
8. **`0, 0` coordinates are a null sentinel**, not a point in the Gulf of
   Guinea. One row in the sample has this.
9. **Out-of-area coordinates are kept, not deleted.** One sample row is at
   `7.2906, 80.6337` — roughly 100 km away. It is retained with
   `in_expected_area=False` rather than discarded. The bounding box
   (`validation.DEFAULT_BOUNDING_BOX`) is an approximation of the Colombo
   service area. **[ASK]** what is the council's actual boundary?
10. **Assets have no coordinates at all**, so reports cannot be geo-joined to
    assets. Road name is the only available join key. This constrains the
    dedup/enrichment stage more than anything else in the data.

## Taxonomies

11. **Crews, work types and report categories are strings + slugs, not enums.**
    That vocabulary belongs to the council and will change; a new crew must not
    be an ingestion error.
12. **Channel, status, urgency, asset type and road class are enums** with an
    unknown/other member and a `*_raw` sibling. Nothing is ever dropped.
13. **`category` and `urgency` are hints, not inputs to required logic.** They
    are null on 60% and 67% of rows respectively.
14. **Job `notes` are closure boilerplate, not descriptions.** Ten distinct
    values across 90 rows (`"Attended and closed."`). Useful for "was it
    actually finished?" (`"Partial, returned next day."`) — useless for dedup.

## PII

15. **`reporter_name`, `reporter_contact` and `photo` filenames are all treated
    as PII.** Photo filenames can encode device or user information, so they are
    excluded from analysis input; `has_photo` is exposed instead.
16. **Free text is assumed contaminated.** Sign-off blocks, inline contact
    details and mentions of relatives all appear. Both structural removal and
    text redaction are applied.
17. **Sign-off blocks are truncated entirely** from the sign-off word to the end
    of the text. Slightly aggressive — it could drop a trailing sentence — but
    losing a clause is preferable to leaking a name to a third-party model.
18. **Name fragments shorter than 4 characters are not redacted**, to avoid
    blanking ordinary words. A resident named "Ann" would not have their
    forename redacted from free text. **[ASK]** acceptable?
19. **No PII is deleted from the internal models** — only from the LLM
    projection. The council still needs to contact residents.

## Scope

20. **This layer does no deduplication, enrichment, prioritisation, crew
    assignment or model calling.** It provides the inputs for those.
21. **`RoadRecord` grouping is exact-key only** (normalized road name). It is
    grouping, not fuzzy matching — resolving `Baeline extension` to
    `Elvitigala Mawatha` is the semantic stage's job, using
    `RoadRecord.search_terms`.
22. **All 50 job road names currently match asset road names exactly.** Nothing
    relies on that continuing to hold.
23. **No persistence.** Ingestion returns in-memory objects; storage is a
    separate concern and not yet decided in the repository.

## Environment

24. **Pydantic v2**, chosen over dataclasses so the same models can be reused
    directly as FastAPI schemas.
25. **No API key is read, stored or referenced anywhere in this layer.** It has
    no network dependency at all.

"""Claude structuring step: turns normalized, redacted resident reports into
candidate operational problems. Adapted from the project's problem_model.py
prototype, wired into the async import pipeline instead of a standalone
script. Resident identity/contact was never extracted by csv_ingest, so it
never reaches the model.
"""
import sqlite3
import pandas as pd
import anthropic

from .config import ANTHROPIC_API_KEY, MODEL, CREW_BY_CAPABILITY

CREW_TYPES = list(CREW_BY_CAPABILITY.keys())
BATCH_SIZE = 40
MAX_BATCH_ATTEMPTS = 3

SYSTEM_PROMPT = f"""You are the triage engine for a municipal council's public
works dispatch system. You are given a batch of raw resident-submitted
reports (phone calls, walk-ins, an online form) about problems around town.
Your job is to turn them into a clean queue of operational problems for a
field coordinator to dispatch crews against.

Ground rules:
- Base every field ONLY on the report text given to you. Never invent a
  location, an issue, or a severity that isn't supported by the reports.
- Group reports together ONLY when they clearly describe the same underlying
  issue (same or clearly matching location AND same kind of problem). When in
  doubt, keep them separate - false grouping hides how many people are
  affected.
- `evidence_count` must equal the number of report IDs listed in
  `source_report_ids`. Every report ID given to you must appear in exactly
  one problem - carry unmatched or unclear reports through as their own
  single-report problem rather than dropping them.
- `priority` starts from the nature of the issue itself: "High" for a genuine
  safety risk (exposed wiring, deep road collapse, flooding blocking a road,
  downed pole). "Medium" for things that will get worse if ignored
  (persistent surface water, a growing pothole). "Low" for cosmetic or
  non-urgent issues.
- Community importance can raise that baseline by one level (never past
  High): a main/arterial road, a bus route, or proximity to a school,
  hospital, clinic or similar raises urgency. Note the reason in
  `priority_reason`.
- Missing key detail (no usable location, no description of what's wrong, or
  too vague to dispatch against) lowers priority by one level (never below
  Low) and the reason must say so, e.g. "Vague report - needs follow-up". Do
  not guess at a location or issue to avoid the drop.
- `priority_reason` is a short (<8 word) human-readable phrase, e.g.
  "Reported safety risk", "Repeated complaint", "Minor / cosmetic",
  "Growing pothole near school", "Vague report - needs follow-up".
- `recommended_crew` must be exactly one of: {", ".join(CREW_TYPES)}. If a
  report genuinely fits none of these, use "Other".
- `title` is a short imperative phrase a coordinator would act on, e.g.
  "Inspect lighting fault and restore service" - not a restatement of the
  raw complaint text.
- `location` is the clearest place name you can extract (road/landmark),
  normalized to match the reference road list if it is one of those roads.
- `confidence` is your confidence (0 to 1) in this grouping/classification, or
  null if you have no meaningful basis for a number. Never invent a
  confidence value to look precise.

Some reports carry a `matched_road` (plus `road_class` and
`nearest_facility`) resolved ahead of time against the council's known road
list - trust it as the road for that report rather than re-deriving one
yourself. For reports with no `matched_road`, use the reference road list
only to normalize spelling and inform the priority judgment - never to invent
a location the report text doesn't support."""

PROBLEMS_TOOL = {
    "name": "submit_problems",
    "description": "Submit the final list of deduplicated operational problems derived from the batch of reports.",
    "input_schema": {
        "type": "object",
        "properties": {
            "problems": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "location": {"type": "string"},
                        "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
                        "priority_reason": {"type": "string"},
                        "recommended_crew": {"type": "string", "enum": CREW_TYPES + ["Other"]},
                        "confidence": {"type": ["number", "null"]},
                        "evidence_count": {"type": "integer"},
                        "source_report_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "title", "location", "priority", "priority_reason",
                        "recommended_crew", "confidence", "evidence_count", "source_report_ids",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["problems"],
        "additionalProperties": False,
    },
    "strict": True,
}


def match_reports_to_roads(reports: pd.DataFrame, assets: pd.DataFrame) -> pd.DataFrame:
    reports_keyed = reports.reset_index(names="_row_id")
    conn = sqlite3.connect(":memory:")
    try:
        reports_keyed.to_sql("reports", conn, index=False)
        assets.to_sql("assets", conn, index=False)
        matches = pd.read_sql(
            """
            WITH roads AS (
                SELECT road_name,
                       GROUP_CONCAT(DISTINCT road_class) AS road_class,
                       GROUP_CONCAT(DISTINCT nearest_facility) AS nearest_facility
                FROM assets WHERE road_name IS NOT NULL GROUP BY road_name
            ),
            ranked AS (
                SELECT r._row_id, ro.road_name, ro.road_class, ro.nearest_facility,
                       ROW_NUMBER() OVER (PARTITION BY r._row_id ORDER BY LENGTH(ro.road_name) DESC) AS rn
                FROM reports r JOIN roads ro
                    ON r.location_text IS NOT NULL
                   AND INSTR(LOWER(r.location_text), LOWER(ro.road_name)) > 0
            )
            SELECT _row_id, road_name AS matched_road, road_class, nearest_facility
            FROM ranked WHERE rn = 1
            """,
            conn,
        )
    finally:
        conn.close()
    return reports_keyed.merge(matches, on="_row_id", how="left").drop(columns="_row_id")


def build_known_roads_context(assets: pd.DataFrame) -> str:
    lines = []
    valid = assets.dropna(subset=["road_name"])
    for road, group in valid.groupby("road_name"):
        classes = sorted(x for x in group["road_class"].dropna().unique().tolist())
        facilities = sorted(x for x in group["nearest_facility"].dropna().unique().tolist())
        details = []
        if classes:
            details.append(", ".join(classes))
        if facilities:
            details.append("near " + ", ".join(facilities))
        lines.append(f"{road} ({'; '.join(details)})" if details else road)
    return "\n".join(sorted(lines)) or "(no known roads supplied)"


def build_report_lines(reports: pd.DataFrame) -> str:
    lines = []
    for _, r in reports.iterrows():
        parts = [f"report_id={r['report_id']}"]
        for field in ("location_text", "matched_road", "road_class", "nearest_facility", "category", "urgency", "received_at", "description"):
            value = r.get(field)
            if value is not None and pd.notna(value) and str(value) != "":
                parts.append(f"{field}={value!r}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _call_model(client, roads_context, batch):
    report_lines = build_report_lines(batch)
    user_content = (
        f"Known roads in this council area (name, class, nearest facility):\n{roads_context}\n\n"
        f"Reports ({len(batch)} total):\n{report_lines}"
    )
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[PROBLEMS_TOOL],
        tool_choice={"type": "tool", "name": "submit_problems"},
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Model output was truncated for a batch; lower BATCH_SIZE.")
    tool_use = next(b for b in response.content if b.type == "tool_use")
    problems = tool_use.input["problems"]
    covered = {rid for p in problems for rid in p["source_report_ids"]}
    missing = set(batch["report_id"]) - covered
    return problems, missing


def _fallback_problem(report) -> dict:
    location = report.get("location_text")
    return {
        "title": "Needs manual triage - dropped by automated triage",
        "location": location if pd.notna(location) else "Unknown",
        "priority": "Medium",
        "priority_reason": "Needs manual review",
        "recommended_crew": "Other",
        "confidence": None,
        "evidence_count": 1,
        "source_report_ids": [report["report_id"]],
    }


def structure_reports(reports: pd.DataFrame, assets: pd.DataFrame, progress_cb=None) -> list:
    """Runs the full report set through Claude in batches. Returns a list of
    problem dicts covering every input report_id (never silently drops one).
    """
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured on the backend.")
    reports = match_reports_to_roads(reports, assets)
    roads_context = build_known_roads_context(assets)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    all_problems = []
    total_batches = max(1, (len(reports) + BATCH_SIZE - 1) // BATCH_SIZE)
    for batch_index, start in enumerate(range(0, len(reports), BATCH_SIZE)):
        remaining = reports.iloc[start:start + BATCH_SIZE]
        for attempt in range(1, MAX_BATCH_ATTEMPTS + 1):
            problems, missing = _call_model(client, roads_context, remaining)
            all_problems.extend(problems)
            if not missing:
                break
            remaining = remaining[remaining["report_id"].isin(missing)]
        else:
            for _, r in remaining.iterrows():
                all_problems.append(_fallback_problem(r))
        if progress_cb:
            progress_cb((batch_index + 1) / total_batches)

    covered = {rid for p in all_problems for rid in p["source_report_ids"]}
    all_ids = set(reports["report_id"])
    missing = all_ids - covered
    if missing:
        for rid in missing:
            row = reports[reports["report_id"] == rid].iloc[0]
            all_problems.append(_fallback_problem(row))
    return all_problems

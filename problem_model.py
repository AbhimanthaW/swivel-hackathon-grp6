"""
Turns raw resident reports (reports.csv) into a deduplicated queue of
operational problems for the Works Dispatch dashboard, using Claude to fill
in what the raw data doesn't give us directly (priority, a clean problem
title/category, which reports are duplicates of each other, and which crew
should handle it).

`status` is intentionally NOT set here - it starts as "Unreviewed" and is
only ever changed by a human coordinator in the app.

Requires: pip install anthropic pandas python-dotenv
Requires: an ANTHROPIC_API_KEY entry in a .env file next to this script
(see .env.example) - it is never hardcoded here.
"""

import json
import sqlite3

import anthropic
import pandas as pd
from dotenv import load_dotenv

REPORTS_CSV = "reports.csv"
ASSETS_CSV = "assets.csv"
OUTPUT_JSON = "problems.json"
OUTPUT_CSV = "problems.csv"

MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 50  # reports per model call - keeps smaller models reliably exhaustive

CREW_TYPES = ["Drainage", "Road Surface", "Lighting & Street Furniture"]

SYSTEM_PROMPT = f"""You are the triage engine for a municipal council's public
works dispatch system. You are given a batch of raw resident-submitted
reports (phone calls, walk-ins, an online form) about problems around town.
Your job is to turn them into a clean queue of operational problems for a
field coordinator to dispatch crews against.

Ground rules:
- Base every field ONLY on the report text given to you. Never invent a
  location, an issue, or a severity that isn't supported by the reports.
- Group reports together ONLY when they clearly describe the same underlying
  issue (same or clearly matching location AND same kind of problem).
  When in doubt, keep them separate - false grouping is worse than a
  duplicate entry, because it hides how many people are affected.
- `evidence_count` must equal the number of report IDs you listed in
  `source_report_ids` for that problem. Every report ID must appear in
  exactly one problem - carry unmatched or unclear reports through as their
  own single-report problem rather than dropping them.
- `priority` starts from the nature of the issue itself: "High" for a genuine
  safety risk (e.g. exposed wiring, deep road collapse, flooding blocking a
  road, downed pole). "Medium" for things that will get worse if ignored
  (persistent surface water, a growing pothole). "Low" for cosmetic or
  non-urgent issues.
- Community importance can raise that baseline by one level (Low->Medium or
  Medium->High, never past High): the reference road list below tells you
  each road's class and the facility nearest to it. Raise priority when the
  location is a main/arterial road, a bus route, or is near a facility where
  people gather or depend on access - a school, hospital, clinic, or similar
  (e.g. a pothole near a school gate is more urgent than the same pothole on
  a quiet residential street). Note the reason for the raise in
  `priority_reason`, e.g. "Growing pothole near school".
- Missing key detail lowers priority instead: if the report has no usable
  location, no description of what's actually wrong, or is too vague to act
  on (can't tell what a coordinator would even dispatch a crew to do), drop
  priority by one level from what the issue type alone would suggest (never
  below "Low") and say why in `priority_reason`, e.g. "Vague report - needs
  follow-up". Do not raise priority to compensate for missing detail, and
  don't guess at a location or issue to avoid the drop - an unclear report is
  genuinely lower-confidence, not equally actionable.
- `priority_reason` is a short (<8 word) human-readable phrase matching the
  UI style, e.g. "Reported safety risk", "Repeated complaint", "Minor / cosmetic",
  "Growing pothole near school", "Vague report - needs follow-up".
- `recommended_crew` must be exactly one of: {", ".join(CREW_TYPES)}.
  If a report genuinely fits none of these, use "Other".
- `title` is a short imperative phrase a coordinator would act on, e.g.
  "Inspect lighting fault and restore service" - not a restatement of the
  raw complaint text.
- `location` is the clearest place name you can extract (road/landmark),
  normalized to match how it's written in the reference road list if it's
  one of those roads.

Some reports already carry a `matched_road` (plus its `road_class` and
`nearest_facility`) - this was resolved ahead of time by matching the
report's location text against the council's known road list, so trust it
as the road for that report rather than re-deriving one yourself; still use
your own judgment for `location`, `priority`, etc. based on the report text.
For reports with no `matched_road`, you will also be given a reference list
of known roads with their class and nearest facility - use it only to
normalize spelling if a report clearly refers to one of those roads and to
inform the priority judgment above, never to invent a location the report
text doesn't support."""

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
                        "recommended_crew": {
                            "type": "string",
                            "enum": CREW_TYPES + ["Other"],
                        },
                        "evidence_count": {"type": "integer"},
                        "source_report_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "title",
                        "location",
                        "priority",
                        "priority_reason",
                        "recommended_crew",
                        "evidence_count",
                        "source_report_ids",
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
    """Pre-joins each report to the most specific known road mentioned in its
    location_text, via an in-memory SQLite query, pulling in that road's
    class and nearest facility so the model doesn't have to guess at (or
    mismatch) the road itself.

    Returns `reports` with three added columns - matched_road, road_class,
    nearest_facility - all None where no known road is mentioned. When a
    report's text contains more than one known road name as a substring
    (e.g. "Old Kesbewa Road" also contains "Kesbewa Road"), the longest
    (most specific) match wins.
    """
    # report_id is not guaranteed unique in the source CSV (this data has a
    # couple of duplicates) - join on row position instead, or a duplicate
    # report_id would cross-join and mismatch one row's location_text with
    # another row's match.
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
                FROM assets
                WHERE road_name IS NOT NULL
                GROUP BY road_name
            ),
            ranked AS (
                SELECT r._row_id,
                       ro.road_name, ro.road_class, ro.nearest_facility,
                       ROW_NUMBER() OVER (
                           PARTITION BY r._row_id ORDER BY LENGTH(ro.road_name) DESC
                       ) AS rn
                FROM reports r
                JOIN roads ro
                    ON r.location_text IS NOT NULL
                   AND INSTR(LOWER(r.location_text), LOWER(ro.road_name)) > 0
            )
            SELECT _row_id,
                   road_name AS matched_road,
                   road_class,
                   nearest_facility
            FROM ranked
            WHERE rn = 1
            """,
            conn,
        )
    finally:
        conn.close()

    return reports_keyed.merge(matches, on="_row_id", how="left").drop(columns="_row_id")


def build_known_roads_context(assets: pd.DataFrame) -> str:
    lines = []
    for road, group in assets.dropna(subset=["road_name"]).groupby("road_name"):
        classes = sorted(group["road_class"].dropna().unique().tolist())
        facilities = sorted(group["nearest_facility"].dropna().unique().tolist())
        details = []
        if classes:
            details.append(", ".join(classes))
        if facilities:
            details.append("near " + ", ".join(facilities))
        lines.append(f"{road} ({'; '.join(details)})" if details else road)
    return "\n".join(sorted(lines))


def build_report_lines(reports: pd.DataFrame) -> str:
    lines = []
    for _, r in reports.iterrows():
        parts = [f"report_id={r['report_id']}"]
        if pd.notna(r.get("location_text")):
            parts.append(f"location_text={r['location_text']!r}")
        if pd.notna(r.get("matched_road")):
            parts.append(f"matched_road={r['matched_road']!r}")
        if pd.notna(r.get("road_class")):
            parts.append(f"road_class={r['road_class']}")
        if pd.notna(r.get("nearest_facility")):
            parts.append(f"nearest_facility={r['nearest_facility']}")
        if pd.notna(r.get("category")) and r["category"]:
            parts.append(f"category={r['category']}")
        if pd.notna(r.get("urgency")) and r["urgency"]:
            parts.append(f"urgency={r['urgency']}")
        if pd.notna(r.get("received_at")):
            parts.append(f"received_at={r['received_at']}")
        if pd.notna(r.get("description")):
            parts.append(f"description={r['description']!r}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


MAX_BATCH_ATTEMPTS = 3


def call_model(client: anthropic.Anthropic, roads_context: str, batch: pd.DataFrame) -> tuple:
    """Runs one batch of reports through the model.

    Returns (problems, missing_report_ids, usage). Raises only if the
    response was truncated (max_tokens) - that means the batch itself is too
    large and retrying won't help. Dropped report IDs are returned rather
    than raised so the caller can retry just those, since that's a per-call
    fluke rather than a batch-size problem.
    """
    report_lines = build_report_lines(batch)
    user_content = (
        f"Known roads in this council area (name, class, nearest facility):\n{roads_context}\n\n"
        f"Reports ({len(batch)} total):\n{report_lines}"
    )

    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[PROBLEMS_TOOL],
        tool_choice={"type": "tool", "name": "submit_problems"},
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "max_tokens":
        raise RuntimeError(
            "Model output was truncated (hit max_tokens) for a batch - the "
            "problems list is incomplete. Lower BATCH_SIZE and rerun."
        )

    tool_use = next(b for b in response.content if b.type == "tool_use")
    problems = tool_use.input["problems"]

    covered = {rid for p in problems for rid in p["source_report_ids"]}
    missing = set(batch["report_id"]) - covered
    return problems, missing, response.usage


def fallback_problem(report: pd.Series) -> dict:
    """Minimal single-report problem for a report the model dropped after
    every retry, so it still reaches the dispatch queue instead of vanishing.
    """
    location = report.get("location_text")
    return {
        "title": "Needs manual triage - dropped by automated triage",
        "location": location if pd.notna(location) else "Unknown",
        "priority": "Medium",
        "priority_reason": "Needs manual review",
        "recommended_crew": "Other",
        "evidence_count": 1,
        "source_report_ids": [report["report_id"]],
    }


def process_batch(client: anthropic.Anthropic, roads_context: str, batch: pd.DataFrame) -> tuple:
    """Runs a batch through the model, retrying only the reports the model
    drops, and backfilling anything still missing after MAX_BATCH_ATTEMPTS.
    Returns (problems, usage_totals).
    """
    problems = []
    cache_created = cache_read = input_tokens = 0
    remaining = batch
    for attempt in range(1, MAX_BATCH_ATTEMPTS + 1):
        part_problems, missing_ids, usage = call_model(client, roads_context, remaining)
        problems.extend(part_problems)
        cache_created += usage.cache_creation_input_tokens
        cache_read += usage.cache_read_input_tokens
        input_tokens += usage.input_tokens
        if not missing_ids:
            break
        remaining = remaining[remaining["report_id"].isin(missing_ids)]
        print(f"  retry {attempt}: {len(missing_ids)} report(s) dropped, re-asking model")
    else:
        for _, r in remaining.iterrows():
            problems.append(fallback_problem(r))
        print(f"  gave up after {MAX_BATCH_ATTEMPTS} attempts, "
              f"backfilled {len(remaining)} report(s) as manual-triage problems")

    usage_totals = {"cache_created": cache_created, "cache_read": cache_read, "input_tokens": input_tokens}
    return problems, usage_totals


def main():
    load_dotenv()  # reads ANTHROPIC_API_KEY from .env into the environment

    reports = pd.read_csv(REPORTS_CSV)
    assets = pd.read_csv(ASSETS_CSV)
    reports = match_reports_to_roads(reports, assets)
    matched = reports["matched_road"].notna().sum()
    print(f"Pre-matched {matched} / {len(reports)} reports to a known road via SQLite")
    roads_context = build_known_roads_context(assets)

    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY loaded above

    problems = []
    cache_created = cache_read = input_tokens = 0
    for start in range(0, len(reports), BATCH_SIZE):
        batch = reports.iloc[start:start + BATCH_SIZE]
        batch_problems, usage = process_batch(client, roads_context, batch)
        problems.extend(batch_problems)
        cache_created += usage["cache_created"]
        cache_read += usage["cache_read"]
        input_tokens += usage["input_tokens"]

    for p in problems:
        p["status"] = "Unreviewed"  # coordinator sets this later, never the model

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(problems, f, indent=2)

    pd.DataFrame(problems).to_csv(OUTPUT_CSV, index=False)

    covered = {rid for p in problems for rid in p["source_report_ids"]}
    all_ids = set(reports["report_id"])
    if covered != all_ids:
        missing = all_ids - covered
        raise RuntimeError(
            f"{len(missing)} report(s) missing from the output problems: "
            f"{sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}"
        )
    print(f"{len(reports)} reports -> {len(problems)} problems")
    print(f"Reports accounted for: {len(covered)} / {len(reports)}")
    print(f"Cache: created={cache_created}, read={cache_read}, input={input_tokens}")


if __name__ == "__main__":
    main()

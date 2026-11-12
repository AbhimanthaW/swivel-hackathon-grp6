"""Inspect a set of council CSV exports from the command line.

    python -m ingestion.cli /path/to/Challenge03-Dataset

Development and debugging aid only - the API uses ``IngestionService`` directly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import IngestionResult, Severity
from .service import IngestionService


def _print_result(result: IngestionResult) -> None:
    quality = result.quality
    print(f"\n=== {result.source_type.value}  ({result.source_name}) ===")
    print(
        f"  rows {result.row_count}  accepted {result.accepted_count}  "
        f"rejected {result.rejected_count}  "
        f"errors {len(result.errors)}  warnings {len(result.warnings)}"
    )
    print(f"  columns: {', '.join(result.detected_columns)}")
    if quality.unused_columns:
        print(f"  unused columns: {', '.join(quality.unused_columns)}")
    if quality.missing_optional_columns:
        print(f"  absent optional: {', '.join(quality.missing_optional_columns)}")

    flags = {
        "missing ids": quality.missing_ids,
        "missing descriptions": quality.missing_descriptions,
        "missing locations": quality.missing_locations,
        "missing timestamps": quality.missing_timestamps,
        "unparseable dates": quality.unparseable_dates,
        "invalid coordinates": quality.invalid_coordinates,
        "with coordinates": quality.records_with_coordinates,
        "with PII": quality.records_with_pii,
    }
    reported = ", ".join(f"{k}: {v}" for k, v in flags.items() if v)
    if reported:
        print(f"  quality: {reported}")
    if quality.duplicate_source_ids:
        print(f"  duplicate source ids: {', '.join(quality.duplicate_source_ids)}")
    if quality.unknown_values:
        print(f"  unknown values: {json.dumps(quality.unknown_values)}")
    if quality.extras:
        print(f"  extras: {json.dumps(quality.extras, default=str)}")

    shown = 0
    for issue in result.issues:
        if issue.severity is Severity.ERROR or shown < 5:
            print(f"  {issue}")
            shown += 1
    remaining = len(result.issues) - shown
    if remaining > 0:
        print(f"  ... and {remaining} more issue(s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing the CSVs")
    parser.add_argument(
        "--json", action="store_true", help="print the machine-readable summary"
    )
    parser.add_argument(
        "--sample-analysis-input",
        action="store_true",
        help="show one PII-safe LLM input to verify the sanitization boundary",
    )
    args = parser.parse_args(argv)

    try:
        bundle = IngestionService().ingest_directory(args.directory)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(bundle.summary(), indent=2, default=str))
    else:
        for result in (bundle.reports, bundle.assets, bundle.jobs):
            _print_result(result)
        print(
            f"\n=== derived ===\n  distinct roads: {len(bundle.roads)}"
            f"\n  jobs coverage: {bundle.jobs_coverage.earliest} -> "
            f"{bundle.jobs_coverage.latest} "
            f"({bundle.jobs_coverage.span_days} days)"
        )

    if args.sample_analysis_input:
        inputs = bundle.analysis_inputs()
        print(f"\n=== analysis input (PII-safe), {len(inputs)} of "
              f"{bundle.reports.accepted_count} reports ===")
        for item in inputs[:3]:
            print(json.dumps(item.to_prompt_dict(), indent=2, default=str))

    return 0 if bundle.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

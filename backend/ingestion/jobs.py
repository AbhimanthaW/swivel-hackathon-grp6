"""Completed works history parsing.

Crew names and work types are deliberately NOT enums: that vocabulary belongs
to the council and will change. They are kept as the original string plus a
stable slug key so downstream code can group without hardcoding today's values.
"""

from __future__ import annotations

from .models import (
    CoverageWindow,
    DataQualityReport,
    IngestionResult,
    IssueCode,
    RawLocation,
    SourceType,
    WorkJob,
)
from .normalizers import (
    clean_line,
    clean_text,
    normalize_location_hint,
    parse_date,
    slugify,
)
from .pipeline import ParseContext, SourceParser, ingest_source
from .reader import RowView, TableSource
from .validation import parse_coordinates, warn


def parse_job_row(view: RowView, ctx: ParseContext) -> WorkJob | None:
    job_id = clean_line(view["job_id"])
    if job_id is None:
        ctx.count("missing_ids")
        ctx.add(
            warn(
                IssueCode.MISSING_ID,
                "Job has no id; an internal id was assigned.",
                row_number=view.row_number,
                field="job_id",
            )
        )

    completed_raw = clean_line(view["completed_at"])
    completed_at, date_error = parse_date(completed_raw)
    if date_error:
        ctx.count("unparseable_dates")
        ctx.add(
            warn(
                IssueCode.UNPARSEABLE_DATE,
                date_error,
                row_number=view.row_number,
                field="completed_at",
                source_id=job_id,
            )
        )
    elif completed_at is None:
        ctx.count("missing_timestamps")
        ctx.add(
            warn(
                IssueCode.MISSING_TIMESTAMP,
                "Job has no completion date; it cannot be used to decide whether a "
                "report was already addressed.",
                row_number=view.row_number,
                field="completed_at",
                source_id=job_id,
            )
        )

    coordinates, coord_issues = parse_coordinates(
        view["latitude"],
        view["longitude"],
        row_number=view.row_number,
        source_id=job_id,
    )
    ctx.add_all(coord_issues)
    if coord_issues:
        ctx.count("invalid_coordinates")
    if coordinates is not None:
        ctx.count("records_with_coordinates")

    road_name = clean_line(view["road_name"])
    location_text = clean_line(view["location_text"]) or road_name
    location = RawLocation(
        text=location_text,
        normalized_text=normalize_location_hint(location_text),
        coordinates=coordinates,
    )
    if location.is_empty:
        ctx.count("missing_locations")
        ctx.add(
            warn(
                IssueCode.MISSING_LOCATION,
                "Job has no road, location or coordinates.",
                row_number=view.row_number,
                source_id=job_id,
            )
        )

    crew = clean_line(view["crew"])
    work_type = clean_line(view["work_type"])
    if work_type is None:
        ctx.add(
            warn(
                IssueCode.MISSING_DESCRIPTION,
                "Job has no work type; crew matching for this job is not possible.",
                row_number=view.row_number,
                field="work_type",
                source_id=job_id,
            )
        )

    return WorkJob(
        ingest_id=ctx.mint_id(view.row_number, job_id),
        source=ctx.metadata(view.row_number),
        raw=dict(view.raw),
        job_id=job_id,
        completed_at=completed_at,
        completed_at_raw=completed_raw,
        crew=crew,
        crew_key=slugify(crew),
        work_type=work_type,
        work_type_key=slugify(work_type),
        road_name=road_name,
        road_name_key=normalize_location_hint(road_name),
        location=location,
        asset_id=clean_line(view["asset_id"]),
        status_raw=clean_line(view["status"]),
        notes=clean_text(view["notes"]),
    )


def coverage_window(jobs: list[WorkJob]) -> CoverageWindow:
    """Measure the date span actually present in the export.

    The brief describes this file as "the previous two weeks". That is a
    property of today's export, not a rule, so it is measured rather than
    assumed anywhere in the code.
    """
    dates = [job.completed_at for job in jobs if job.completed_at is not None]
    if not dates:
        return CoverageWindow()
    return CoverageWindow(earliest=min(dates), latest=max(dates))


def _job_quality(
    records: list[WorkJob], quality: DataQualityReport, ctx: ParseContext
) -> None:
    window = coverage_window(records)
    crews: dict[str, int] = {}
    work_types: dict[str, int] = {}
    roads: set[str] = set()
    for record in records:
        if record.crew:
            crews[record.crew] = crews.get(record.crew, 0) + 1
        if record.work_type:
            work_types[record.work_type] = work_types.get(record.work_type, 0) + 1
        if record.road_name_key:
            roads.add(record.road_name_key)
    quality.extras.update(
        {
            "coverage_earliest": window.earliest.isoformat() if window.earliest else None,
            "coverage_latest": window.latest.isoformat() if window.latest else None,
            "coverage_span_days": window.span_days,
            "crew_counts": crews,
            "work_type_counts": work_types,
            "distinct_roads": len(roads),
            "with_asset_reference": sum(1 for r in records if r.asset_id),
        }
    )


JOBS_PARSER: SourceParser[WorkJob] = SourceParser(
    source_type=SourceType.JOBS,
    parse_row=parse_job_row,
    source_id_attr="job_id",
    quality_hook=_job_quality,
)


def ingest_jobs(
    source: TableSource,
    *,
    source_name: str | None = None,
    verify_source_type: bool = True,
) -> IngestionResult[WorkJob]:
    """Ingest a completed works history export."""
    return ingest_source(
        source,
        JOBS_PARSER,
        source_name=source_name,
        verify_source_type=verify_source_type,
    )

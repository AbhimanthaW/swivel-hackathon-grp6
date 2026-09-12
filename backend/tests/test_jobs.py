"""Jobs history parsing and the measured coverage window."""

from __future__ import annotations

from datetime import date

from ingestion import IssueCode, coverage_window, ingest_jobs


def test_valid_jobs_csv(jobs_csv: str) -> None:
    result = ingest_jobs(jobs_csv, source_name="jobs-history.csv")

    assert result.ok
    assert result.accepted_count == 3
    first = result.records[0]
    assert first.job_id == "JOB-0001"
    assert first.completed_at == date(2026, 8, 25)
    assert first.crew == "Lighting & Street Furniture"
    assert first.work_type == "Streetlight column repair"
    assert first.road_name == "Temple Lane"
    assert first.notes == "Completed."


def test_crews_and_work_types_are_slugged_not_enumerated(jobs_csv: str) -> None:
    """The council owns this vocabulary; new values must not be rejected."""
    result = ingest_jobs(jobs_csv)

    assert result.records[0].crew_key == "lighting_and_street_furniture"
    assert result.records[0].work_type_key == "streetlight_column_repair"
    assert not result.warnings, "new crew/work-type values are data, not errors"


def test_unseen_crew_is_accepted() -> None:
    csv = (
        "job_id,completed_date,crew,work_type,road_name\n"
        "JOB-1,2026-09-01,Vegetation & Trees,Tree limb removal,Jawatta Road\n"
    )
    result = ingest_jobs(csv)

    assert result.accepted_count == 1
    assert result.records[0].crew == "Vegetation & Trees"
    assert result.records[0].crew_key == "vegetation_and_trees"


def test_coverage_window_is_measured_not_assumed(jobs_csv: str) -> None:
    result = ingest_jobs(jobs_csv)
    window = coverage_window(result.records)

    assert window.earliest == date(2026, 8, 25)
    assert window.latest == date(2026, 9, 7)
    assert window.span_days == 14
    assert result.quality.extras["coverage_span_days"] == 14


def test_coverage_window_adapts_to_a_longer_export() -> None:
    csv = (
        "job_id,completed_date,crew,work_type,road_name\n"
        "JOB-1,2026-06-01,Drainage,Drain clearing,Galle Road\n"
        "JOB-2,2026-09-07,Drainage,Drain clearing,Galle Road\n"
    )
    window = coverage_window(ingest_jobs(csv).records)

    assert window.span_days == 99, "nothing may hardcode a two-week window"


def test_empty_coverage_window() -> None:
    window = coverage_window([])

    assert window.earliest is None
    assert window.span_days is None


def test_alternative_headers_and_optional_fields() -> None:
    csv = (
        "Work Order,Date Completed,Team,Job Type,Street,Asset Ref,Outcome,Remarks\n"
        "WO-1,01/09/2026,Drainage,Gully cleaning,Galle Road,MMC-DRA-1,complete,All clear\n"
    )
    result = ingest_jobs(csv)

    assert result.ok
    record = result.records[0]
    assert record.job_id == "WO-1"
    assert record.completed_at == date(2026, 9, 1)
    assert record.crew == "Drainage"
    assert record.asset_id == "MMC-DRA-1"
    assert record.status_raw == "complete"
    assert record.notes == "All clear"


def test_missing_completion_date_is_warned() -> None:
    csv = (
        "job_id,completed_date,crew,work_type,road_name\n"
        "JOB-1,,Drainage,Drain clearing,Galle Road\n"
        "JOB-2,31/02/2026,Drainage,Drain clearing,Galle Road\n"
    )
    result = ingest_jobs(csv)

    assert result.accepted_count == 2
    assert result.quality.missing_timestamps == 1
    assert result.quality.unparseable_dates == 1
    assert any(i.code is IssueCode.MISSING_TIMESTAMP for i in result.warnings)


def test_missing_work_type_is_flagged() -> None:
    csv = "job_id,completed_date,crew,work_type,road_name\nJOB-1,2026-09-01,Drainage,,Galle Road\n"
    result = ingest_jobs(csv)

    assert result.accepted_count == 1
    assert result.records[0].work_type is None
    assert any("work type" in i.message for i in result.warnings)


def test_road_name_key_supports_matching_reports_to_jobs(jobs_csv: str) -> None:
    """Ingestion only supplies the key; it does not perform the match."""
    result = ingest_jobs(jobs_csv)
    keys = {r.road_name_key for r in result.records}

    assert keys == {"temple lane", "kirula road", "havelock road"}

"""The service facade: explicit slots, detection convenience and bundles."""

from __future__ import annotations

from pathlib import Path

from ingestion import IngestionService, IssueCode, SourceType


def test_ingest_bundle(service: IngestionService, reports_csv, assets_csv, jobs_csv) -> None:
    bundle = service.ingest_bundle(
        reports=reports_csv,
        assets=assets_csv,
        jobs=jobs_csv,
        reports_name="reports.csv",
        assets_name="assets.csv",
        jobs_name="jobs-history.csv",
    )

    assert bundle.ok
    assert bundle.reports.accepted_count == 4
    assert bundle.assets.accepted_count == 4
    assert bundle.jobs.accepted_count == 3
    assert len(bundle.roads) == 3
    assert bundle.jobs_coverage.span_days == 14


def test_bundle_summary_shape(service: IngestionService, reports_csv, assets_csv, jobs_csv) -> None:
    bundle = service.ingest_bundle(reports=reports_csv, assets=assets_csv, jobs=jobs_csv)
    summary = bundle.summary()

    assert set(summary["sources"]) == {"reports", "assets", "jobs"}
    assert summary["derived"]["distinct_roads"] == 3
    assert summary["sources"]["reports"]["quality"]["duplicate_source_ids"] == []


def test_bundle_analysis_inputs_are_sanitized(
    service: IngestionService, reports_csv, assets_csv, jobs_csv
) -> None:
    from ingestion import find_pii_leaks

    bundle = service.ingest_bundle(reports=reports_csv, assets=assets_csv, jobs=jobs_csv)
    inputs = bundle.analysis_inputs()

    assert len(inputs) == 4
    assert find_pii_leaks(inputs, ["Alex Fernando", "Cara Perera", "0114425687"]) == []


def test_explicit_source_type_wins(service: IngestionService, reports_csv: str) -> None:
    result = service.ingest(reports_csv, SourceType.REPORTS)

    assert result.source_type is SourceType.REPORTS
    assert result.accepted_count == 4


def test_ingest_auto(service: IngestionService, jobs_csv: str) -> None:
    result = service.ingest_auto(jobs_csv, source_name="anything.csv")

    assert result.source_type is SourceType.JOBS
    assert result.accepted_count == 3


def test_ingest_auto_refuses_an_unknown_file(service: IngestionService) -> None:
    result = service.ingest_auto("invoice_number,supplier\nINV-1,Acme\n")

    assert result.fatal
    assert any(i.code is IssueCode.SOURCE_TYPE_UNDETECTED for i in result.errors)


def test_identify(service: IngestionService, assets_csv: str) -> None:
    detection = service.identify(assets_csv)

    assert detection.source_type is SourceType.ASSETS
    assert detection.is_confident


def test_ingest_path(service: IngestionService, tmp_path: Path, jobs_csv: str) -> None:
    path = tmp_path / "history.csv"
    path.write_text(jobs_csv, encoding="utf-8")

    result = service.ingest_path(path, SourceType.JOBS)

    assert result.source_name == "history.csv"
    assert result.accepted_count == 3


def test_ingest_directory(
    service: IngestionService, tmp_path: Path, reports_csv, assets_csv, jobs_csv
) -> None:
    (tmp_path / "a.csv").write_text(reports_csv, encoding="utf-8")
    (tmp_path / "b.csv").write_text(assets_csv, encoding="utf-8")
    (tmp_path / "c.csv").write_text(jobs_csv, encoding="utf-8")

    bundle = service.ingest_directory(tmp_path)

    assert bundle.reports.source_name == "a.csv"
    assert bundle.assets.source_name == "b.csv"
    assert bundle.jobs.source_name == "c.csv"


def test_swapped_upload_slots_are_reported(
    service: IngestionService, reports_csv, assets_csv, jobs_csv
) -> None:
    """The realistic UI failure: the user picks the wrong file for a slot."""
    bundle = service.ingest_bundle(reports=assets_csv, assets=reports_csv, jobs=jobs_csv)

    assert not bundle.ok
    mismatches = [i for i in bundle.issues if i.code is IssueCode.SOURCE_TYPE_MISMATCH]
    assert len(mismatches) == 2


def test_records_serialize_without_their_raw_row(
    service: IngestionService, reports_csv: str
) -> None:
    result = service.ingest(reports_csv, SourceType.REPORTS)
    record = result.records[0]

    assert record.raw["reporter_contact"] == "0114425687", "kept for traceability"
    payload = record.model_dump_json()
    assert "reporter_contact" not in payload, "raw column names must never serialize"
    assert "0114425687" in payload, "the value is still reachable via reporter.phone"
    assert "ingest_id" in payload

    # detected_columns/column_mapping intentionally DO expose the source
    # headers - that is what makes the mapping debuggable in the UI.
    assert "reporter_contact" in result.model_dump_json()


def test_real_council_dataset_ingests_cleanly(
    service: IngestionService, council_dataset_dir
) -> None:
    """Opt-in: skipped when the sample export is not on this machine."""
    bundle = service.ingest_directory(council_dataset_dir)

    assert bundle.ok
    assert bundle.reports.rejected_count == 0
    assert bundle.assets.rejected_count == 0
    assert bundle.jobs.rejected_count == 0
    # The two genuine report_id collisions must be detected, not silently merged.
    assert len(bundle.reports.quality.duplicate_source_ids) == 2
    assert len({r.ingest_id for r in bundle.reports.records}) == bundle.reports.accepted_count

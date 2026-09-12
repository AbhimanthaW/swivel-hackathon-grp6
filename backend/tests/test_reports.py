"""Resident report parsing: happy path, schema drift and dirty rows."""

from __future__ import annotations

from datetime import datetime

from ingestion import (
    IssueCode,
    ReportChannel,
    ReportStatus,
    UrgencyHint,
    ingest_reports,
)


def test_valid_csv_produces_canonical_reports(reports_csv: str) -> None:
    result = ingest_reports(reports_csv, source_name="reports.csv")

    assert result.ok
    assert not result.errors
    assert result.row_count == 4
    assert result.accepted_count == 4
    assert result.rejected_count == 0
    assert result.source_name == "reports.csv"

    first = result.records[0]
    assert first.source_report_id == "MR-000001"
    assert first.channel is ReportChannel.PHONE
    assert first.reported_at == datetime(2026, 9, 8, 8, 38, 7)
    assert first.status is ReportStatus.NEW
    assert first.location.text == "Havelock Road, by the bus halt"
    assert first.reporter.name == "Alex Fernando"
    assert first.reporter.phone == "0114425687"

    second = result.records[1]
    assert second.urgency is UrgencyHint.HIGH
    assert second.has_photo
    assert second.status is ReportStatus.TRIAGED
    assert second.reporter.email == "bea.silva@example.com"


def test_internal_ids_are_unique_and_deterministic(reports_csv: str) -> None:
    first = ingest_reports(reports_csv)
    second = ingest_reports(reports_csv)

    ids = [r.ingest_id for r in first.records]
    assert len(set(ids)) == len(ids)
    assert ids == [r.ingest_id for r in second.records]


def test_reordered_columns(reports_csv: str) -> None:
    lines = reports_csv.strip().split("\n")
    header = lines[0].split(",")
    reordered_header = ",".join(reversed(header))
    # Only the header order is asserted on; reorder a single simple row too.
    body = "new,,,,Surface water is not draining away.,,,\"Havelock Road\",0114425687,Alex Fernando,2026-09-08T08:38:07,phone,MR-000001"
    result = ingest_reports(f"{reordered_header}\n{body}")

    assert result.ok
    assert result.accepted_count == 1
    record = result.records[0]
    assert record.source_report_id == "MR-000001"
    assert record.channel is ReportChannel.PHONE
    assert record.location.text == "Havelock Road"


def test_renamed_columns_via_aliases() -> None:
    csv = (
        "Reference,Source,Date Received,Resident Name,Email,Address,Details,Priority\n"
        "REF-9,Web,2026-09-08,Dana Roy,dana@example.com,Galle Road,Pothole here,urgent\n"
    )
    result = ingest_reports(csv)

    assert result.ok
    record = result.records[0]
    assert record.source_report_id == "REF-9"
    assert record.channel is ReportChannel.WEB_FORM
    assert record.reporter.email == "dana@example.com"
    assert record.description == "Pothole here"
    assert record.urgency is UrgencyHint.HIGH


def test_optional_columns_missing() -> None:
    csv = "report_id,received_at,location_text,description\nMR-1,2026-09-08,Park Road,Blocked drain\n"
    result = ingest_reports(csv)

    assert result.ok
    record = result.records[0]
    assert record.channel is ReportChannel.UNKNOWN
    assert record.urgency is None
    assert record.reporter.has_pii is False
    assert "urgency" in result.quality.missing_optional_columns
    assert any(i.code is IssueCode.MISSING_SEMANTIC_COLUMN for i in result.warnings)


def test_missing_required_column_fails_the_file() -> None:
    csv = "channel,received_at,description\nphone,2026-09-08,Pothole\n"
    result = ingest_reports(csv)

    assert result.fatal
    assert result.accepted_count == 0
    assert any(i.code is IssueCode.MISSING_REQUIRED_COLUMN for i in result.errors)


def test_extra_columns_are_reported_but_ignored(reports_csv: str) -> None:
    lines = reports_csv.strip().split("\n")
    lines[0] += ",internal_officer_note,legacy_ref"
    for index in range(1, len(lines)):
        lines[index] += ",secret,LEG-1"
    result = ingest_reports("\n".join(lines))

    assert result.ok
    assert result.accepted_count == 4
    assert result.quality.unused_columns == ["internal_officer_note", "legacy_ref"]
    assert any(i.code is IssueCode.UNUSED_COLUMN for i in result.warnings)
    # Retained for traceability, never surfaced as a domain field.
    assert result.records[0].raw["legacy_ref"] == "LEG-1"
    assert "legacy_ref" not in result.records[0].model_dump()


def test_malformed_dates_are_warned_not_fatal() -> None:
    csv = (
        "report_id,received_at,location_text,description\n"
        "MR-1,not a date,Park Road,Blocked drain\n"
        "MR-2,08/09/2026 09:28,Park Road,Blocked drain\n"
        "MR-3,,Park Road,Blocked drain\n"
    )
    result = ingest_reports(csv)

    assert result.accepted_count == 3
    assert result.quality.unparseable_dates == 1
    assert result.quality.missing_timestamps == 1
    assert result.records[0].reported_at is None
    assert result.records[0].reported_at_raw == "not a date"
    # Day-first, not month-first.
    assert result.records[1].reported_at == datetime(2026, 9, 8, 9, 28)
    assert any(i.code is IssueCode.UNPARSEABLE_DATE for i in result.warnings)


def test_empty_descriptions_and_locations_are_counted(reports_csv: str) -> None:
    result = ingest_reports(reports_csv)

    assert result.quality.missing_descriptions == 0
    assert result.quality.missing_locations == 1  # MR-000004 has neither text nor coords
    assert any(i.code is IssueCode.MISSING_LOCATION for i in result.warnings)


def test_empty_description_column_values() -> None:
    csv = (
        "report_id,received_at,location_text,description\n"
        "MR-1,2026-09-08,Park Road,\n"
        "MR-2,2026-09-08,Park Road,   \n"
        "MR-3,2026-09-08,Park Road,N/A\n"
    )
    result = ingest_reports(csv)

    assert result.accepted_count == 3
    assert result.quality.missing_descriptions == 3
    assert all(r.description is None for r in result.records)


def test_duplicate_source_ids_are_kept_and_flagged() -> None:
    csv = (
        "report_id,received_at,location_text,description\n"
        "MR-1,2026-09-08,Park Road,Pothole on Park Road\n"
        "MR-1,2026-09-08,Galle Road,Broken bench on Galle Road\n"
        "MR-2,2026-09-08,Reid Avenue,Blocked drain\n"
    )
    result = ingest_reports(csv)

    assert result.accepted_count == 3, "a colliding id must not drop a report"
    assert result.quality.duplicate_source_ids == ["MR-1"]
    assert [r.source_id_is_duplicate for r in result.records] == [True, True, False]
    assert len({r.ingest_id for r in result.records}) == 3
    assert any(i.code is IssueCode.DUPLICATE_SOURCE_ID for i in result.warnings)


def test_missing_id_is_warned_but_row_kept() -> None:
    csv = "report_id,received_at,location_text,description\n,2026-09-08,Park Road,Pothole\n"
    result = ingest_reports(csv)

    assert result.accepted_count == 1
    assert result.quality.missing_ids == 1
    assert result.records[0].ingest_id
    assert any(i.code is IssueCode.MISSING_ID for i in result.warnings)


def test_malformed_rows_do_not_abort_the_import() -> None:
    csv = (
        "report_id,received_at,location_text,description\n"
        "MR-1,2026-09-08,Park Road,Good row\n"
        "MR-2,2026-09-08,Park Road,Too many,EXTRA,EXTRA2\n"
        "MR-3,2026-09-08\n"
        "\n"
        "MR-4,2026-09-08,Park Road,Another good row\n"
    )
    result = ingest_reports(csv)

    assert result.accepted_count == 4
    assert [r.source_report_id for r in result.records] == ["MR-1", "MR-2", "MR-3", "MR-4"]
    assert any(i.code is IssueCode.RAGGED_ROW for i in result.warnings)
    assert result.records[2].location.text is None


def test_multiline_quoted_description_is_preserved(reports_csv: str) -> None:
    result = ingest_reports(reports_csv)
    record = result.records[2]

    assert "\n" in record.description
    assert record.description.startswith("There is a pothole")


def test_unknown_channel_keeps_raw_value() -> None:
    csv = (
        "report_id,channel,received_at,location_text,description\n"
        "MR-1,carrier pigeon,2026-09-08,Park Road,Pothole\n"
    )
    result = ingest_reports(csv)

    record = result.records[0]
    assert record.channel is ReportChannel.UNKNOWN
    assert record.channel_raw == "carrier pigeon"
    assert "channel" in result.quality.unknown_values
    assert any(i.code is IssueCode.UNKNOWN_ENUM_VALUE for i in result.warnings)


def test_semicolon_delimited_export() -> None:
    csv = "report_id;received_at;location_text;description\nMR-1;2026-09-08;Park Road;Blocked drain\n"
    result = ingest_reports(csv)

    assert result.accepted_count == 1
    assert result.records[0].location.text == "Park Road"


def test_bom_and_padded_headers() -> None:
    csv = "\ufeff Report ID , Received At , Location , Description \nMR-1,2026-09-08,Park Road,Pothole\n"
    result = ingest_reports(csv)

    assert result.accepted_count == 1
    assert result.records[0].source_report_id == "MR-1"


def test_empty_file() -> None:
    result = ingest_reports("")

    assert result.fatal
    assert any(i.code is IssueCode.EMPTY_FILE for i in result.errors)


def test_header_only_file() -> None:
    result = ingest_reports("report_id,received_at,location_text,description\n")

    assert result.row_count == 0
    assert result.accepted_count == 0
    assert not result.errors

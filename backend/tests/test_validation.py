"""Schema mapping, source-type detection and normalization primitives."""

from __future__ import annotations

from datetime import datetime

import pytest

from ingestion import (
    IssueCode,
    SourceType,
    detect_source_type,
    get_schema,
    ingest_assets,
    ingest_jobs,
    ingest_reports,
    resolve_columns,
)
from ingestion.mappings import header_key, iter_schemas
from ingestion.normalizers import (
    clean_line,
    clean_text,
    is_null_token,
    normalize_location_hint,
    parse_datetime,
    slugify,
)
from ingestion.validation import parse_coordinates

# --------------------------------------------------------------------------
# Schema registry integrity
# --------------------------------------------------------------------------


def test_no_alias_is_claimed_by_two_fields() -> None:
    """Import-time guard; asserted here so a bad alias fails a test, not prod."""
    for schema in iter_schemas():
        seen: dict[str, str] = {}
        for spec in schema.fields:
            for alias in spec.alias_keys:
                assert seen.get(alias, spec.name) == spec.name, (
                    f"{schema.source_type}: alias {alias!r} is ambiguous between "
                    f"{seen[alias]!r} and {spec.name!r}"
                )
                seen[alias] = spec.name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Report ID", "report_id"),
        ("report_id", "report_id"),
        ("REPORT-ID", "report_id"),
        ("  Report  Id  ", "report_id"),
        ("Report.Id", "report_id"),
    ],
)
def test_header_key_normalization(raw: str, expected: str) -> None:
    assert header_key(raw) == expected


def test_separator_insensitive_matching() -> None:
    column_map = resolve_columns(["reportid", "receivedat"], get_schema(SourceType.REPORTS))

    assert column_map.mapping["source_report_id"] == "reportid"
    assert column_map.mapping["reported_at"] == "receivedat"


def test_ambiguous_columns_resolve_by_alias_preference() -> None:
    column_map = resolve_columns(
        ["report_id", "reference", "description"], get_schema(SourceType.REPORTS)
    )

    assert column_map.mapping["source_report_id"] == "report_id"
    assert "reference" in column_map.unused
    assert any(i.code is IssueCode.AMBIGUOUS_COLUMN for i in column_map.issues)


def test_duplicate_headers_are_renamed_not_dropped() -> None:
    result = ingest_reports(
        "report_id,description,description\nMR-1,first,second\n"
    )

    assert result.accepted_count == 1
    assert result.records[0].description == "first"
    assert any(i.code is IssueCode.DUPLICATE_HEADER for i in result.warnings)


# --------------------------------------------------------------------------
# Source type detection
# --------------------------------------------------------------------------


def test_detects_each_source_type(reports_csv: str, assets_csv: str, jobs_csv: str) -> None:
    cases = [
        (reports_csv, SourceType.REPORTS),
        (assets_csv, SourceType.ASSETS),
        (jobs_csv, SourceType.JOBS),
    ]
    for csv, expected in cases:
        headers = csv.strip().split("\n")[0].split(",")
        detection = detect_source_type(headers)
        assert detection.source_type is expected, detection.reason
        assert detection.confidence > 0.5


def test_detection_refuses_unrelated_schemas() -> None:
    detection = detect_source_type(["invoice_number", "supplier", "vat_amount"])

    assert detection.source_type is None
    assert not detection.is_confident


def test_detection_ignores_filenames(assets_csv: str) -> None:
    """Detection is column-based; the caller's naming is irrelevant."""
    result = ingest_reports(assets_csv, source_name="reports.csv")

    assert any(i.code is IssueCode.SOURCE_TYPE_MISMATCH for i in result.issues)


def test_wrong_source_type_gives_a_useful_message(assets_csv: str) -> None:
    result = ingest_reports(assets_csv)

    assert result.fatal
    mismatch = next(i for i in result.errors if i.code is IssueCode.SOURCE_TYPE_MISMATCH)
    assert "assets" in mismatch.message
    assert "reports" in mismatch.message


def test_jobs_file_supplied_as_assets(jobs_csv: str) -> None:
    result = ingest_assets(jobs_csv)

    assert not result.records
    assert any(i.code is IssueCode.SOURCE_TYPE_MISMATCH for i in result.errors)


def test_reports_file_supplied_as_jobs(reports_csv: str) -> None:
    result = ingest_jobs(reports_csv)

    assert any(i.code is IssueCode.SOURCE_TYPE_MISMATCH for i in result.issues)


# --------------------------------------------------------------------------
# Normalization primitives
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   ", "N/A", "n/a", "NULL", "-", "unknown"])
def test_null_tokens(value: str) -> None:
    assert is_null_token(value)
    assert clean_line(value) is None


def test_clean_text_preserves_paragraphs_but_trims_noise() -> None:
    assert clean_text("  Line one  \r\n\r\n\r\n  Line two \t ") == "Line one\n\nLine two"


def test_clean_line_collapses_newlines() -> None:
    assert clean_line("Havelock\n Road ") == "Havelock Road"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-08T08:38:07", datetime(2026, 9, 8, 8, 38, 7)),
        ("2026-09-08 08:38", datetime(2026, 9, 8, 8, 38)),
        ("08/09/2026 09:28", datetime(2026, 9, 8, 9, 28)),
        ("08-09-2026", datetime(2026, 9, 8)),
        ("2026-09-08", datetime(2026, 9, 8)),
    ],
)
def test_supported_date_formats(raw: str, expected: datetime) -> None:
    parsed, error = parse_datetime(raw)
    assert error is None
    assert parsed == expected


def test_unparseable_date_returns_a_message() -> None:
    parsed, error = parse_datetime("last Tuesday")
    assert parsed is None
    assert "unrecognised" in error


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Temple Ln", "temple lane"),
        ("Temple Lane", "temple lane"),
        ("  REID   AVE  ", "reid avenue"),
        ("Muhandiram Pl.", "muhandiram place"),
        ("R A de Mel Mawata", "r a de mel mawatha"),
        ("Station Rd", "station road"),
    ],
)
def test_location_hint_normalization(raw: str, expected: str) -> None:
    assert normalize_location_hint(raw) == expected


def test_location_hint_does_not_collapse_qualifiers() -> None:
    """Ingestion must not decide that these are the same place."""
    assert normalize_location_hint("Kirula Road") != normalize_location_hint(
        "Kirula Road near the junction"
    )


def test_location_hint_does_not_correct_spelling() -> None:
    """Typo correction is the semantic stage's job, not ingestion's."""
    assert normalize_location_hint("Sation Passage") == "sation passage"


def test_original_location_text_is_always_preserved() -> None:
    result = ingest_reports(
        "report_id,location_text,description\nMR-1,  Temple Ln  ,Pothole\n"
    )
    location = result.records[0].location

    assert location.text == "Temple Ln"
    assert location.normalized_text == "temple lane"


def test_slugify() -> None:
    assert slugify("Lighting & Street Furniture") == "lighting_and_street_furniture"
    assert slugify("  ") is None


# --------------------------------------------------------------------------
# Coordinates
# --------------------------------------------------------------------------


def test_valid_coordinates() -> None:
    coords, issues = parse_coordinates("6.884565", "79.866178", row_number=2)

    assert coords.latitude == 6.884565
    assert coords.in_expected_area
    assert not issues


def test_zero_zero_is_a_null_sentinel() -> None:
    coords, issues = parse_coordinates("0.0", "0.0", row_number=2)

    assert coords is None
    assert issues[0].code is IssueCode.INVALID_COORDINATE


def test_out_of_area_coordinates_are_kept_but_flagged() -> None:
    coords, issues = parse_coordinates("7.2906", "80.6337", row_number=2)

    assert coords is not None, "uncertainty is preserved, not deleted"
    assert coords.in_expected_area is False
    assert issues[0].code is IssueCode.COORDINATE_OUT_OF_AREA


def test_partial_coordinates_are_ignored() -> None:
    coords, issues = parse_coordinates("6.9", "", row_number=2)

    assert coords is None
    assert issues[0].code is IssueCode.PARTIAL_COORDINATE


def test_non_numeric_coordinates() -> None:
    coords, issues = parse_coordinates("north", "east", row_number=2)

    assert coords is None
    assert all(i.code is IssueCode.INVALID_COORDINATE for i in issues)

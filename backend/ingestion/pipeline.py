"""Shared ingestion machinery.

The three parsers differ only in how they build one record from one row.
Everything around that - reading, mapping, validating, counting, reporting -
lives here so the parsers stay small and comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import blake2b
from typing import Generic, Protocol, TypeVar

from .mappings import get_schema
from .models import (
    CanonicalRecord,
    DataQualityReport,
    IngestionResult,
    Issue,
    IssueCode,
    SourceMetadata,
    SourceType,
)
from .reader import RawTable, RowView, TableSource, read_table
from .validation import (
    ColumnMap,
    check_declared_source_type,
    error,
    resolve_columns,
    warn,
)

RecordT = TypeVar("RecordT", bound=CanonicalRecord)

_ID_PREFIXES = {
    SourceType.REPORTS: "rep",
    SourceType.ASSETS: "ast",
    SourceType.JOBS: "job",
}


@dataclass
class ParseContext:
    """Per-file state handed to a row parser."""

    source_type: SourceType
    source_name: str | None
    ingested_at: datetime
    issues: list[Issue] = field(default_factory=list)
    unknown_values: dict[str, list[str]] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def add(self, *issues: Issue | None) -> None:
        for issue in issues:
            if issue is not None:
                self.issues.append(issue)
                if issue.code is IssueCode.UNKNOWN_ENUM_VALUE and issue.field:
                    bucket = self.unknown_values.setdefault(issue.field, [])
                    if issue.message not in bucket:
                        bucket.append(issue.message)

    def add_all(self, issues: list[Issue]) -> None:
        self.add(*issues)

    def count(self, key: str, amount: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + amount

    def note_unknown(self, field_name: str, raw: str) -> None:
        bucket = self.unknown_values.setdefault(field_name, [])
        if raw not in bucket:
            bucket.append(raw)

    def mint_id(self, row_number: int, source_id: str | None) -> str:
        """Deterministic internal identifier.

        Derived from source type, source id and row ordinal so that
        re-ingesting the same export produces the same ids, while the two
        genuine ``report_id`` collisions in the sample data still get distinct
        internal identities.
        """
        seed = f"{self.source_type.value}|{source_id or ''}|{row_number}"
        digest = blake2b(seed.encode("utf-8"), digest_size=6).hexdigest()
        return f"{_ID_PREFIXES[self.source_type]}_{digest}"

    def metadata(self, row_number: int) -> SourceMetadata:
        return SourceMetadata(
            source_type=self.source_type,
            source_name=self.source_name,
            row_number=row_number,
            ingested_at=self.ingested_at,
        )


class RowParser(Protocol[RecordT]):
    def __call__(self, view: RowView, ctx: ParseContext) -> RecordT | None: ...


class QualityHook(Protocol[RecordT]):
    def __call__(
        self, records: list[RecordT], quality: DataQualityReport, ctx: ParseContext
    ) -> None: ...


@dataclass
class SourceParser(Generic[RecordT]):
    """Binds a source type to its row parser and optional quality hook."""

    source_type: SourceType
    parse_row: RowParser[RecordT]
    source_id_attr: str
    duplicate_flag_attr: str | None = None
    quality_hook: QualityHook[RecordT] | None = None


def ingest_table(
    table: RawTable,
    parser: SourceParser[RecordT],
    *,
    source_name: str | None = None,
    verify_source_type: bool = True,
    ingested_at: datetime | None = None,
) -> IngestionResult[RecordT]:
    """Run one parser over an already-read table."""
    source_type = parser.source_type
    schema = get_schema(source_type)
    ctx = ParseContext(
        source_type=source_type,
        source_name=source_name,
        ingested_at=ingested_at or datetime.now(timezone.utc),
    )

    if table.is_empty:
        ctx.add(
            error(
                IssueCode.EMPTY_FILE,
                "The file is empty or has no header row.",
            )
        )
        return _finalise(source_type, source_name, table, ColumnMap(schema=schema), [], ctx)

    for duplicate in table.duplicate_headers:
        ctx.add(
            warn(
                IssueCode.DUPLICATE_HEADER,
                f"Column {duplicate!r} appears more than once; later copies were "
                "renamed and ignored.",
                field=duplicate,
            )
        )

    if verify_source_type:
        ctx.add(*check_declared_source_type(table.headers, source_type))

    column_map = resolve_columns(table.headers, schema)
    ctx.add(*column_map.issues)

    if not column_map.usable:
        return _finalise(source_type, source_name, table, column_map, [], ctx)

    records: list[RecordT] = []
    seen_ids: dict[str, int] = {}
    duplicate_ids: list[str] = []

    for raw_row in table.rows:
        view = RowView(raw_row, column_map.mapping)

        if view.is_blank:
            ctx.add(
                warn(
                    IssueCode.EMPTY_ROW,
                    "Row is empty; skipped.",
                    row_number=view.row_number,
                )
            )
            continue

        if raw_row.overflow:
            ctx.add(
                warn(
                    IssueCode.RAGGED_ROW,
                    f"Row has {len(raw_row.overflow)} more value(s) than there are "
                    "columns; the extra values were ignored.",
                    row_number=view.row_number,
                )
            )
        if raw_row.missing_fields:
            ctx.add(
                warn(
                    IssueCode.RAGGED_ROW,
                    f"Row is missing values for {', '.join(raw_row.missing_fields)}; "
                    "treated as empty.",
                    row_number=view.row_number,
                )
            )

        try:
            record = parser.parse_row(view, ctx)
        except Exception as exc:  # a bad row must not abort the import
            ctx.add(
                error(
                    IssueCode.ROW_REJECTED,
                    f"Row could not be parsed and was rejected: {exc}",
                    row_number=view.row_number,
                )
            )
            continue

        if record is None:
            continue

        source_id = getattr(record, parser.source_id_attr, None)
        if source_id:
            first_seen = seen_ids.get(source_id)
            if first_seen is not None:
                if source_id not in duplicate_ids:
                    duplicate_ids.append(source_id)
                ctx.add(
                    warn(
                        IssueCode.DUPLICATE_SOURCE_ID,
                        f"Source id {source_id!r} already appeared on row "
                        f"{first_seen}. Both rows were kept under distinct internal "
                        "ids; the source id is not a reliable key.",
                        row_number=view.row_number,
                        source_id=source_id,
                    )
                )
            else:
                seen_ids[source_id] = view.row_number
        records.append(record)

    if parser.duplicate_flag_attr and duplicate_ids:
        duplicate_set = set(duplicate_ids)
        for record in records:
            if getattr(record, parser.source_id_attr, None) in duplicate_set:
                setattr(record, parser.duplicate_flag_attr, True)

    result = _finalise(source_type, source_name, table, column_map, records, ctx)
    result.quality.duplicate_source_ids = duplicate_ids
    if parser.quality_hook is not None:
        parser.quality_hook(records, result.quality, ctx)
    return result


def ingest_source(
    source: TableSource,
    parser: SourceParser[RecordT],
    *,
    source_name: str | None = None,
    verify_source_type: bool = True,
    ingested_at: datetime | None = None,
) -> IngestionResult[RecordT]:
    """Read and ingest in one step."""
    return ingest_table(
        read_table(source),
        parser,
        source_name=source_name,
        verify_source_type=verify_source_type,
        ingested_at=ingested_at,
    )


def _finalise(
    source_type: SourceType,
    source_name: str | None,
    table: RawTable,
    column_map: ColumnMap,
    records: list[RecordT],
    ctx: ParseContext,
) -> IngestionResult[RecordT]:
    row_count = len(table.rows)
    accepted = len(records)
    quality = DataQualityReport(
        rows_loaded=row_count,
        rows_accepted=accepted,
        rows_rejected=max(row_count - accepted, 0),
        missing_ids=ctx.counters.get("missing_ids", 0),
        missing_descriptions=ctx.counters.get("missing_descriptions", 0),
        missing_locations=ctx.counters.get("missing_locations", 0),
        missing_timestamps=ctx.counters.get("missing_timestamps", 0),
        unparseable_dates=ctx.counters.get("unparseable_dates", 0),
        invalid_coordinates=ctx.counters.get("invalid_coordinates", 0),
        records_with_coordinates=ctx.counters.get("records_with_coordinates", 0),
        records_with_pii=ctx.counters.get("records_with_pii", 0),
        unknown_values=dict(ctx.unknown_values),
        unused_columns=list(column_map.unused),
        missing_optional_columns=list(column_map.missing_optional),
    )
    return IngestionResult[RecordT](
        source_type=source_type,
        source_name=source_name,
        records=records,
        row_count=row_count,
        accepted_count=accepted,
        rejected_count=max(row_count - accepted, 0),
        detected_columns=list(table.headers),
        column_mapping=dict(column_map.mapping),
        issues=list(ctx.issues),
        quality=quality,
    )

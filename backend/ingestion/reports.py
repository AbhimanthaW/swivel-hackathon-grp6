"""Resident report parsing.

Turns one row of a reporting-system export into a :class:`ResidentReport`.
All PII is funnelled into the nested ``reporter`` object so the LLM boundary
can drop it structurally.
"""

from __future__ import annotations

from .mappings import CHANNEL_ALIASES, STATUS_ALIASES, URGENCY_ALIASES
from .models import (
    ContactKind,
    DataQualityReport,
    IngestionResult,
    IssueCode,
    RawLocation,
    ReportChannel,
    ReporterContact,
    ReportStatus,
    ResidentReport,
    SourceType,
    UrgencyHint,
)
from .normalizers import (
    clean_line,
    clean_text,
    looks_like_email,
    looks_like_phone,
    normalize_location_hint,
    parse_datetime,
    slugify,
)
from .pipeline import ParseContext, SourceParser, ingest_source
from .reader import RowView, TableSource
from .validation import parse_coordinates, resolve_enum, warn


def _parse_contact(view: RowView, ctx: ParseContext) -> ReporterContact:
    """Split a possibly-union contact column into typed PII fields.

    The sample export puts emails and phone numbers in a single
    ``reporter_contact`` column; other exports may use separate columns.
    Both shapes are supported.
    """
    name = clean_line(view["reporter_name"])
    email = clean_line(view["reporter_email"])
    phone = clean_line(view["reporter_phone"])
    combined = clean_line(view["reporter_contact"])

    kind: ContactKind | None = None
    if combined:
        if looks_like_email(combined):
            kind = ContactKind.EMAIL
            email = email or combined
        elif looks_like_phone(combined):
            kind = ContactKind.PHONE
            phone = phone or combined
        else:
            kind = ContactKind.OTHER
    elif email:
        kind = ContactKind.EMAIL
    elif phone:
        kind = ContactKind.PHONE

    contact = ReporterContact(
        name=name,
        email=email,
        phone=phone,
        raw_contact=combined,
        contact_kind=kind,
    )
    if contact.has_pii:
        ctx.count("records_with_pii")
    return contact


def parse_report_row(view: RowView, ctx: ParseContext) -> ResidentReport | None:
    source_id = clean_line(view["source_report_id"])
    if source_id is None:
        ctx.count("missing_ids")
        ctx.add(
            warn(
                IssueCode.MISSING_ID,
                "Report has no source id; an internal id was assigned.",
                row_number=view.row_number,
                field="source_report_id",
            )
        )

    channel_raw = clean_line(view["channel"])
    channel, channel_issue = resolve_enum(
        channel_raw,
        CHANNEL_ALIASES,
        field_name="channel",
        row_number=view.row_number,
        source_id=source_id,
    )
    ctx.add(channel_issue)
    if channel_issue is not None and channel_raw:
        ctx.note_unknown("channel", channel_raw)

    reported_at_raw = clean_line(view["reported_at"])
    reported_at, date_error = parse_datetime(reported_at_raw)
    if date_error:
        ctx.count("unparseable_dates")
        ctx.add(
            warn(
                IssueCode.UNPARSEABLE_DATE,
                date_error,
                row_number=view.row_number,
                field="reported_at",
                source_id=source_id,
            )
        )
    elif reported_at is None:
        ctx.count("missing_timestamps")
        ctx.add(
            warn(
                IssueCode.MISSING_TIMESTAMP,
                "Report has no timestamp; recency cannot be assessed.",
                row_number=view.row_number,
                field="reported_at",
                source_id=source_id,
            )
        )

    description = clean_text(view["description"])
    if description is None:
        ctx.count("missing_descriptions")
        ctx.add(
            warn(
                IssueCode.MISSING_DESCRIPTION,
                "Report has no description; only its category and location can be "
                "used downstream.",
                row_number=view.row_number,
                field="description",
                source_id=source_id,
            )
        )

    coordinates, coord_issues = parse_coordinates(
        view["latitude"],
        view["longitude"],
        row_number=view.row_number,
        source_id=source_id,
    )
    ctx.add_all(coord_issues)
    if coord_issues:
        ctx.count("invalid_coordinates")
    if coordinates is not None:
        ctx.count("records_with_coordinates")

    location_text = clean_line(view["location_text"])
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
                "Report has neither location text nor coordinates.",
                row_number=view.row_number,
                field="location_text",
                source_id=source_id,
            )
        )

    urgency_raw = clean_line(view["urgency"])
    urgency_key, urgency_issue = resolve_enum(
        urgency_raw,
        URGENCY_ALIASES,
        field_name="urgency",
        row_number=view.row_number,
        source_id=source_id,
    )
    ctx.add(urgency_issue)
    if urgency_issue is not None and urgency_raw:
        ctx.note_unknown("urgency", urgency_raw)

    status_raw = clean_line(view["status"])
    status, status_issue = resolve_enum(
        status_raw,
        STATUS_ALIASES,
        field_name="status",
        row_number=view.row_number,
        source_id=source_id,
    )
    ctx.add(status_issue)
    if status_issue is not None and status_raw:
        ctx.note_unknown("status", status_raw)

    category_raw = clean_line(view["category"])

    return ResidentReport(
        ingest_id=ctx.mint_id(view.row_number, source_id),
        source=ctx.metadata(view.row_number),
        raw=dict(view.raw),
        source_report_id=source_id,
        channel=channel or ReportChannel.UNKNOWN,  # type: ignore[arg-type]
        channel_raw=channel_raw,
        reported_at=reported_at,
        reported_at_raw=reported_at_raw,
        description=description,
        location=location,
        reporter=_parse_contact(view, ctx),
        category_raw=category_raw,
        category_key=slugify(category_raw),
        urgency=UrgencyHint(urgency_key) if urgency_key else None,
        urgency_raw=urgency_raw,
        status=status or ReportStatus.UNKNOWN,  # type: ignore[arg-type]
        status_raw=status_raw,
        photo_reference=clean_line(view["photo"]),
    )


def _report_quality(
    records: list[ResidentReport], quality: DataQualityReport, ctx: ParseContext
) -> None:
    channels: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for record in records:
        channels[record.channel.value] = channels.get(record.channel.value, 0) + 1
        statuses[record.status.value] = statuses.get(record.status.value, 0) + 1
    quality.extras.update(
        {
            "channel_counts": channels,
            "status_counts": statuses,
            "with_category": sum(1 for r in records if r.category_raw),
            "with_urgency": sum(1 for r in records if r.urgency is not None),
            "with_photo": sum(1 for r in records if r.has_photo),
            "coordinates_out_of_area": sum(
                1
                for r in records
                if r.location.coordinates is not None
                and not r.location.coordinates.in_expected_area
            ),
        }
    )


REPORTS_PARSER: SourceParser[ResidentReport] = SourceParser(
    source_type=SourceType.REPORTS,
    parse_row=parse_report_row,
    source_id_attr="source_report_id",
    duplicate_flag_attr="source_id_is_duplicate",
    quality_hook=_report_quality,
)


def ingest_reports(
    source: TableSource,
    *,
    source_name: str | None = None,
    verify_source_type: bool = True,
) -> IngestionResult[ResidentReport]:
    """Ingest a resident reports export."""
    return ingest_source(
        source,
        REPORTS_PARSER,
        source_name=source_name,
        verify_source_type=verify_source_type,
    )

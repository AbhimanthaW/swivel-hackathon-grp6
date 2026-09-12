"""Canonical internal domain models.

These are the *only* representation the rest of the application should use.
No raw CSV header ever appears above this layer.

Pydantic is used (rather than dataclasses) because these models will be reused
directly as FastAPI response/request schemas once the API contract lands.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Enumerations
#
# Only genuinely closed sets are modelled as enums, and every one of them has
# an UNKNOWN member plus a sibling ``*_raw`` field on the owning model. Open
# council taxonomies (crew names, work types, report categories) are kept as
# strings + slugs because that vocabulary is owned by the council and will
# change without notice.
# --------------------------------------------------------------------------


class SourceType(str, Enum):
    """The three external exports this subsystem understands."""

    REPORTS = "reports"
    ASSETS = "assets"
    JOBS = "jobs"


class ReportChannel(str, Enum):
    WEB_FORM = "web_form"
    PHONE = "phone"
    MOBILE_APP = "mobile_app"
    EMAIL = "email"
    UNKNOWN = "unknown"


class ReportStatus(str, Enum):
    NEW = "new"
    TRIAGED = "triaged"
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class UrgencyHint(str, Enum):
    """Resident/operator supplied urgency. A *hint*, never authoritative."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssetType(str, Enum):
    BUS_SHELTER = "bus_shelter"
    STREETLIGHT = "streetlight"
    DRAIN = "drain"
    FOOTPATH = "footpath"
    OTHER = "other"


class RoadClass(str, Enum):
    MAIN_ROAD = "main_road"
    BUS_ROUTE = "bus_route"
    RESIDENTIAL = "residential"
    LANE = "lane"
    OTHER = "other"


class ContactKind(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    OTHER = "other"


class Severity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class IssueCode(str, Enum):
    """Stable machine-readable codes so the UI can group/localise messages."""

    # File / schema level
    EMPTY_FILE = "empty_file"
    NO_HEADER = "no_header"
    MISSING_REQUIRED_COLUMN = "missing_required_column"
    MISSING_SEMANTIC_COLUMN = "missing_semantic_column"
    AMBIGUOUS_COLUMN = "ambiguous_column"
    UNUSED_COLUMN = "unused_column"
    DUPLICATE_HEADER = "duplicate_header"
    SOURCE_TYPE_MISMATCH = "source_type_mismatch"
    SOURCE_TYPE_UNDETECTED = "source_type_undetected"

    # Row level
    RAGGED_ROW = "ragged_row"
    MISSING_ID = "missing_id"
    DUPLICATE_SOURCE_ID = "duplicate_source_id"
    MISSING_DESCRIPTION = "missing_description"
    MISSING_LOCATION = "missing_location"
    MISSING_TIMESTAMP = "missing_timestamp"
    UNPARSEABLE_DATE = "unparseable_date"
    UNPARSEABLE_NUMBER = "unparseable_number"
    INVALID_COORDINATE = "invalid_coordinate"
    COORDINATE_OUT_OF_AREA = "coordinate_out_of_area"
    PARTIAL_COORDINATE = "partial_coordinate"
    UNKNOWN_ENUM_VALUE = "unknown_enum_value"
    EMPTY_ROW = "empty_row"
    ROW_REJECTED = "row_rejected"


class Issue(BaseModel):
    """A single validation finding. Warnings keep the row; errors reject it."""

    model_config = ConfigDict(frozen=True)

    code: IssueCode
    severity: Severity
    message: str
    row_number: int | None = None
    field: str | None = None
    source_id: str | None = None

    def __str__(self) -> str:  # pragma: no cover - convenience only
        where = f" (row {self.row_number})" if self.row_number else ""
        return f"[{self.severity.value}:{self.code.value}]{where} {self.message}"


# --------------------------------------------------------------------------
# Shared value objects
# --------------------------------------------------------------------------


class Coordinates(BaseModel):
    """A geographic point taken verbatim from the source.

    ``in_expected_area`` is False when the point falls outside the council's
    configured bounding box. The value is retained rather than discarded so
    that downstream code can decide - ingestion preserves uncertainty.
    """

    model_config = ConfigDict(frozen=True)

    latitude: float
    longitude: float
    in_expected_area: bool = True


class RawLocation(BaseModel):
    """Location as supplied, plus safe hints. No resolution is attempted."""

    model_config = ConfigDict(frozen=True)

    text: str | None = Field(
        default=None, description="Original location text, whitespace-trimmed only."
    )
    normalized_text: str | None = Field(
        default=None,
        description="Lowercased, punctuation-collapsed, road abbreviations expanded. "
        "A matching hint only - never a resolved location.",
    )
    coordinates: Coordinates | None = None

    @property
    def is_empty(self) -> bool:
        return self.text is None and self.coordinates is None


class ReporterContact(BaseModel):
    """All resident PII, isolated in one nested object.

    Keeping this in a single container means the LLM boundary can drop it
    structurally instead of relying on every call site to remember.
    """

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    raw_contact: str | None = None
    contact_kind: ContactKind | None = None

    @property
    def has_pii(self) -> bool:
        return any((self.name, self.email, self.phone, self.raw_contact))


class SourceMetadata(BaseModel):
    """Provenance for one ingested row."""

    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    source_name: str | None = None
    row_number: int
    ingested_at: datetime


class CanonicalRecord(BaseModel):
    """Base for every ingested entity.

    ``raw`` retains the original row for debugging and traceability but is
    excluded from every serialization, so raw council headers can never leak
    into an API response or an LLM prompt.
    """

    model_config = ConfigDict(populate_by_name=True)

    ingest_id: str = Field(description="Stable internal identifier minted at ingest.")
    source: SourceMetadata
    raw: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)


# --------------------------------------------------------------------------
# 1. Resident reports
# --------------------------------------------------------------------------


class ResidentReport(CanonicalRecord):
    source_report_id: str | None = None
    source_id_is_duplicate: bool = Field(
        default=False,
        description="True when this source report id also appears on another row. "
        "The sample export contains genuine collisions, so the source id is not "
        "usable as a primary key.",
    )

    channel: ReportChannel = ReportChannel.UNKNOWN
    channel_raw: str | None = None

    reported_at: datetime | None = None
    reported_at_raw: str | None = None

    description: str | None = None
    location: RawLocation = Field(default_factory=RawLocation)

    reporter: ReporterContact = Field(default_factory=ReporterContact)

    category_raw: str | None = Field(
        default=None, description="Council-supplied category, as exported."
    )
    category_key: str | None = None
    urgency: UrgencyHint | None = None
    urgency_raw: str | None = None
    status: ReportStatus = ReportStatus.UNKNOWN
    status_raw: str | None = None
    photo_reference: str | None = None

    @property
    def has_photo(self) -> bool:
        return self.photo_reference is not None

    @property
    def has_description(self) -> bool:
        return bool(self.description)


# --------------------------------------------------------------------------
# 2. Assets / locations
# --------------------------------------------------------------------------


class RoadContext(BaseModel):
    """Road-level attributes that the asset export repeats on every row."""

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    name_key: str | None = None
    aliases: tuple[str, ...] = ()
    road_class: RoadClass | None = None
    road_class_raw: str | None = None
    ward: str | None = None
    nearest_facility: str | None = None
    facility_distance_m: int | None = None


class CouncilAsset(CanonicalRecord):
    asset_id: str | None = None
    asset_type: AssetType = AssetType.OTHER
    asset_type_raw: str | None = None
    name: str | None = None
    road: RoadContext = Field(default_factory=RoadContext)
    location: RawLocation = Field(default_factory=RawLocation)
    owner: str | None = Field(
        default=None, description="Owning body, when the export states one."
    )


class RoadRecord(BaseModel):
    """De-duplicated road view derived from the asset export.

    The asset CSV has mixed grain (one row per asset, road attributes
    repeated). This collapses it back into the road entity that later
    location enrichment actually needs.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    name_key: str
    aliases: tuple[str, ...] = ()
    road_class: RoadClass | None = None
    ward: str | None = None
    nearest_facility: str | None = None
    facility_distance_m: int | None = None
    asset_ids: tuple[str, ...] = ()
    asset_types: tuple[AssetType, ...] = ()

    @property
    def search_terms(self) -> tuple[str, ...]:
        """Name plus aliases, for later text matching. No matching done here."""
        return (self.name, *self.aliases)


# --------------------------------------------------------------------------
# 3. Jobs history
# --------------------------------------------------------------------------


class WorkJob(CanonicalRecord):
    job_id: str | None = None
    completed_at: date | None = None
    completed_at_raw: str | None = None
    crew: str | None = None
    crew_key: str | None = None
    work_type: str | None = None
    work_type_key: str | None = None
    road_name: str | None = None
    road_name_key: str | None = None
    location: RawLocation = Field(default_factory=RawLocation)
    asset_id: str | None = None
    status_raw: str | None = None
    notes: str | None = None


class CoverageWindow(BaseModel):
    """Observed date span of a jobs export.

    The brief describes the file as "the previous two weeks", but that is a
    property of today's export, not a rule. It is measured, never assumed.
    """

    model_config = ConfigDict(frozen=True)

    earliest: date | None = None
    latest: date | None = None

    @property
    def span_days(self) -> int | None:
        if self.earliest is None or self.latest is None:
            return None
        return (self.latest - self.earliest).days + 1


# --------------------------------------------------------------------------
# Ingestion result
# --------------------------------------------------------------------------


class DataQualityReport(BaseModel):
    """Lightweight per-source quality summary for debugging and the UI."""

    rows_loaded: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    missing_ids: int = 0
    duplicate_source_ids: list[str] = Field(default_factory=list)
    missing_descriptions: int = 0
    missing_locations: int = 0
    missing_timestamps: int = 0
    unparseable_dates: int = 0
    invalid_coordinates: int = 0
    records_with_coordinates: int = 0
    unknown_values: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Unrecognised enum values, keyed by canonical field name.",
    )
    unused_columns: list[str] = Field(default_factory=list)
    missing_optional_columns: list[str] = Field(default_factory=list)
    records_with_pii: int = 0
    extras: dict[str, Any] = Field(
        default_factory=dict, description="Source-specific metrics."
    )


RecordT = TypeVar("RecordT", bound=CanonicalRecord)


class IngestionResult(BaseModel, Generic[RecordT]):
    """The single return shape for every ingestion call.

    A malformed row is rejected and reported; it never aborts the import.
    """

    source_type: SourceType
    source_name: str | None = None
    records: list[RecordT] = Field(default_factory=list)
    row_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    detected_columns: list[str] = Field(default_factory=list)
    column_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="canonical field name -> actual column header used.",
    )
    issues: list[Issue] = Field(default_factory=list)
    quality: DataQualityReport = Field(default_factory=DataQualityReport)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """True when the file was usable, even if some rows were rejected."""
        return not self.errors or self.accepted_count > 0

    @property
    def fatal(self) -> bool:
        """True when nothing could be ingested at all."""
        return self.accepted_count == 0 and bool(self.errors)

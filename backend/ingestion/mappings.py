"""Central schema mapping registry.

This is the ONLY place in the codebase that knows about external CSV column
names. Adding support for a new council export means adding aliases here, not
touching parser code.

Design:
  * Every canonical field declares an ordered tuple of aliases (preference
    order). Headers are normalized before matching, so casing, spacing,
    punctuation and separator style are all free.
  * ``requirement`` distinguishes "the file is unusable without this" from
    "the domain needs this but a row can be rejected" from "nice to have".
  * Alias collisions inside a schema are a programming error and are caught
    at import time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Iterable, Mapping

from .models import AssetType, ReportChannel, ReportStatus, RoadClass, SourceType

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def header_key(header: str) -> str:
    """Normalize a raw CSV header to a match key.

    ``"Report ID"``, ``"report_id"``, ``"REPORT-ID"`` and ``"  Report Id  "``
    all collapse to ``"report_id"``.
    """
    return _NON_ALNUM.sub("_", header.strip().lower()).strip("_")


def _compact(key: str) -> str:
    """Separator-insensitive form: ``report_id`` -> ``reportid``."""
    return key.replace("_", "")


class Requirement(str, Enum):
    #: Without this column the file cannot be ingested at all.
    REQUIRED = "required"
    #: The domain needs this; rows missing a value are flagged, and the file
    #: is flagged if the column is absent entirely, but ingestion continues.
    SEMANTIC = "semantic"
    #: Purely optional enrichment.
    OPTIONAL = "optional"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    aliases: tuple[str, ...]
    requirement: Requirement = Requirement.OPTIONAL
    description: str = ""

    @property
    def alias_keys(self) -> tuple[str, ...]:
        return tuple(header_key(a) for a in self.aliases)


@dataclass(frozen=True)
class SourceSchema:
    source_type: SourceType
    fields: tuple[FieldSpec, ...]
    #: Canonical fields whose presence strongly implies this source type.
    signature_fields: tuple[str, ...]
    label: str = ""

    _by_name: dict[str, FieldSpec] = field(default_factory=dict, compare=False)
    _alias_index: dict[str, tuple[str, int]] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        by_name: dict[str, FieldSpec] = {}
        alias_index: dict[str, tuple[str, int]] = {}
        for spec in self.fields:
            if spec.name in by_name:
                raise ValueError(f"{self.source_type}: duplicate field {spec.name!r}")
            by_name[spec.name] = spec
            for priority, alias in enumerate(spec.alias_keys):
                for key in (alias, _compact(alias)):
                    existing = alias_index.get(key)
                    if existing is not None and existing[0] != spec.name:
                        raise ValueError(
                            f"{self.source_type}: alias {key!r} is claimed by both "
                            f"{existing[0]!r} and {spec.name!r}"
                        )
                    # keep the strongest (lowest) priority for this field
                    if existing is None or priority < existing[1]:
                        alias_index[key] = (spec.name, priority)
        object.__setattr__(self, "_by_name", by_name)
        object.__setattr__(self, "_alias_index", alias_index)

        missing = set(self.signature_fields) - set(by_name)
        if missing:
            raise ValueError(f"{self.source_type}: unknown signature fields {missing}")

    def spec(self, name: str) -> FieldSpec:
        return self._by_name[name]

    def match(self, header: str) -> tuple[str, int] | None:
        """Resolve a raw header to ``(canonical_field, alias_priority)``."""
        key = header_key(header)
        hit = self._alias_index.get(key)
        if hit is None:
            hit = self._alias_index.get(_compact(key))
        return hit

    def names_by_requirement(self, requirement: Requirement) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.requirement is requirement)


# --------------------------------------------------------------------------
# Enum alias tables
#
# Keys are ``normalize_enum_value`` outputs. Unrecognised values are never
# dropped - the parser keeps the raw string and records an issue.
# --------------------------------------------------------------------------

CHANNEL_ALIASES: Final[Mapping[str, ReportChannel]] = {
    "web_form": ReportChannel.WEB_FORM,
    "webform": ReportChannel.WEB_FORM,
    "web": ReportChannel.WEB_FORM,
    "website": ReportChannel.WEB_FORM,
    "online": ReportChannel.WEB_FORM,
    "online_form": ReportChannel.WEB_FORM,
    "portal": ReportChannel.WEB_FORM,
    "phone": ReportChannel.PHONE,
    "telephone": ReportChannel.PHONE,
    "call": ReportChannel.PHONE,
    "call_centre": ReportChannel.PHONE,
    "call_center": ReportChannel.PHONE,
    "hotline": ReportChannel.PHONE,
    "mobile_app": ReportChannel.MOBILE_APP,
    "app": ReportChannel.MOBILE_APP,
    "mobile": ReportChannel.MOBILE_APP,
    "ios": ReportChannel.MOBILE_APP,
    "android": ReportChannel.MOBILE_APP,
    "email": ReportChannel.EMAIL,
    "e_mail": ReportChannel.EMAIL,
    "mail": ReportChannel.EMAIL,
    "inbox": ReportChannel.EMAIL,
}

STATUS_ALIASES: Final[Mapping[str, ReportStatus]] = {
    "new": ReportStatus.NEW,
    "open": ReportStatus.OPEN,
    "received": ReportStatus.NEW,
    "logged": ReportStatus.NEW,
    "triaged": ReportStatus.TRIAGED,
    "triage": ReportStatus.TRIAGED,
    "assessed": ReportStatus.TRIAGED,
    "in_progress": ReportStatus.OPEN,
    "closed": ReportStatus.CLOSED,
    "resolved": ReportStatus.CLOSED,
    "completed": ReportStatus.CLOSED,
    "done": ReportStatus.CLOSED,
}

URGENCY_ALIASES: Final[Mapping[str, str]] = {
    "low": "low",
    "l": "low",
    "3": "low",
    "minor": "low",
    "routine": "low",
    "medium": "medium",
    "med": "medium",
    "m": "medium",
    "2": "medium",
    "normal": "medium",
    "moderate": "medium",
    "standard": "medium",
    "high": "high",
    "h": "high",
    "1": "high",
    "urgent": "high",
    "critical": "high",
    "emergency": "high",
    "major": "high",
}

ASSET_TYPE_ALIASES: Final[Mapping[str, AssetType]] = {
    "bus_shelter": AssetType.BUS_SHELTER,
    "bus_stop": AssetType.BUS_SHELTER,
    "bus_halt": AssetType.BUS_SHELTER,
    "shelter": AssetType.BUS_SHELTER,
    "streetlight": AssetType.STREETLIGHT,
    "street_light": AssetType.STREETLIGHT,
    "street_lamp": AssetType.STREETLIGHT,
    "lamp_column": AssetType.STREETLIGHT,
    "lighting_column": AssetType.STREETLIGHT,
    "drain": AssetType.DRAIN,
    "gully": AssetType.DRAIN,
    "storm_drain": AssetType.DRAIN,
    "manhole": AssetType.DRAIN,
    "culvert": AssetType.DRAIN,
    "footpath": AssetType.FOOTPATH,
    "pavement": AssetType.FOOTPATH,
    "sidewalk": AssetType.FOOTPATH,
    "walkway": AssetType.FOOTPATH,
}

ROAD_CLASS_ALIASES: Final[Mapping[str, RoadClass]] = {
    "main_road": RoadClass.MAIN_ROAD,
    "main": RoadClass.MAIN_ROAD,
    "a_road": RoadClass.MAIN_ROAD,
    "arterial": RoadClass.MAIN_ROAD,
    "primary": RoadClass.MAIN_ROAD,
    "trunk": RoadClass.MAIN_ROAD,
    "bus_route": RoadClass.BUS_ROUTE,
    "busroute": RoadClass.BUS_ROUTE,
    "secondary": RoadClass.BUS_ROUTE,
    "residential": RoadClass.RESIDENTIAL,
    "local": RoadClass.RESIDENTIAL,
    "estate": RoadClass.RESIDENTIAL,
    "lane": RoadClass.LANE,
    "alley": RoadClass.LANE,
    "track": RoadClass.LANE,
    "unclassified": RoadClass.LANE,
}


# --------------------------------------------------------------------------
# Source schemas
# --------------------------------------------------------------------------

_R = Requirement

REPORTS_SCHEMA = SourceSchema(
    source_type=SourceType.REPORTS,
    label="Resident reports export",
    signature_fields=(
        "source_report_id",
        "channel",
        "reported_at",
        "description",
        "location_text",
        "reporter_name",
    ),
    fields=(
        FieldSpec(
            "source_report_id",
            ("report_id", "reference", "report_reference", "report_ref", "case_id",
             "case_reference", "ticket_id", "ticket_ref", "record_id", "id"),
            _R.REQUIRED,
            "Identifier from the reporting system. Not guaranteed unique.",
        ),
        FieldSpec(
            "channel",
            ("channel", "report_channel", "source_channel", "contact_channel",
             "medium", "origin", "source"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "reported_at",
            ("received_at", "reported_at", "created_at", "date_received",
             "received_date", "report_date", "logged_at", "timestamp",
             "date_time", "datetime", "received", "created"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "reporter_name",
            ("reporter_name", "resident_name", "caller_name", "contact_name",
             "customer_name", "reported_by", "full_name", "name"),
            _R.OPTIONAL,
            "PII.",
        ),
        FieldSpec(
            "reporter_contact",
            ("reporter_contact", "contact", "contact_details", "contact_info",
             "contact_value"),
            _R.OPTIONAL,
            "PII. Union column - may hold an email or a phone number.",
        ),
        FieldSpec(
            "reporter_email",
            ("reporter_email", "email", "email_address", "contact_email", "e_mail"),
            _R.OPTIONAL,
            "PII.",
        ),
        FieldSpec(
            "reporter_phone",
            ("reporter_phone", "phone", "telephone", "phone_number",
             "contact_number", "mobile", "msisdn", "tel"),
            _R.OPTIONAL,
            "PII.",
        ),
        FieldSpec(
            "location_text",
            ("location_text", "location", "location_description", "location_desc",
             "reported_location", "street_address", "address", "place", "site",
             "where"),
            _R.SEMANTIC,
        ),
        FieldSpec("latitude", ("latitude", "lat", "y_coord", "y"), _R.OPTIONAL),
        FieldSpec(
            "longitude",
            ("longitude", "lng", "lon", "long", "x_coord", "x"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "description",
            ("description", "problem_description", "resident_text", "details",
             "comments", "complaint", "narrative", "free_text", "message",
             "body", "text", "notes"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "category",
            ("category", "report_type", "problem_category", "service_type",
             "issue_type", "subject", "service", "type"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "urgency",
            ("urgency", "urgency_level", "priority", "severity"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "photo",
            ("photo", "photo_filename", "image", "image_url", "attachment",
             "attachments", "picture", "media"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "status",
            ("status", "report_status", "current_status", "state"),
            _R.OPTIONAL,
        ),
    ),
)


ASSETS_SCHEMA = SourceSchema(
    source_type=SourceType.ASSETS,
    label="Council asset / location export",
    signature_fields=("asset_id", "asset_type", "road_name", "road_class", "ward"),
    fields=(
        FieldSpec(
            "asset_id",
            ("asset_id", "asset_ref", "asset_reference", "asset_number", "uprn",
             "reference", "ref", "id"),
            _R.REQUIRED,
        ),
        FieldSpec(
            "asset_type",
            ("asset_type", "asset_category", "asset_class", "feature_type",
             "category", "type"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "asset_name",
            ("asset_name", "asset_label", "label", "title", "name"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "road_name",
            ("road_name", "street_name", "highway_name", "thoroughfare", "road",
             "street"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "road_aliases",
            ("also_known_as", "known_as", "alternative_name", "alternate_name",
             "alt_name", "other_name", "local_name", "alias", "aka"),
            _R.OPTIONAL,
            "Semicolon/comma separated alternative road names.",
        ),
        FieldSpec(
            "road_class",
            ("road_class", "road_classification", "classification",
             "road_hierarchy", "hierarchy", "road_type", "class"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "ward",
            ("ward", "district", "locality", "neighbourhood", "neighborhood",
             "suburb", "area", "zone"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "address",
            ("address", "full_address", "site_address", "location_text",
             "location"),
            _R.OPTIONAL,
        ),
        FieldSpec("latitude", ("latitude", "lat", "y_coord", "y"), _R.OPTIONAL),
        FieldSpec(
            "longitude",
            ("longitude", "lng", "lon", "long", "x_coord", "x"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "nearest_facility",
            ("nearest_facility", "nearby_facility", "nearest_poi", "nearest_school",
             "facility_name", "facility", "poi"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "facility_distance_m",
            ("facility_distance_m", "facility_distance", "distance_to_facility",
             "facility_dist", "distance_m"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "owner",
            ("owner", "owned_by", "ownership", "responsible_body",
             "maintained_by", "authority", "council_owned"),
            _R.OPTIONAL,
        ),
    ),
)


JOBS_SCHEMA = SourceSchema(
    source_type=SourceType.JOBS,
    label="Completed works history export",
    signature_fields=("job_id", "completed_at", "crew", "work_type"),
    fields=(
        FieldSpec(
            "job_id",
            ("job_id", "job_number", "job_ref", "work_order_id", "work_order",
             "workorder", "wo_number", "task_id", "reference", "ref", "id"),
            _R.REQUIRED,
        ),
        FieldSpec(
            "completed_at",
            ("completed_date", "completed_at", "completion_date", "date_completed",
             "closed_date", "closed_at", "finished_at", "job_date", "completed",
             "date"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "crew",
            ("crew", "crew_name", "assigned_crew", "team_name", "team", "gang",
             "squad", "resource"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "work_type",
            ("work_type", "works_type", "job_type", "task_type", "work_category",
             "activity_type", "activity", "work", "type"),
            _R.SEMANTIC,
        ),
        FieldSpec(
            "road_name",
            ("road_name", "street_name", "thoroughfare", "road", "street"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "location_text",
            ("location_text", "location_description", "site_address", "location",
             "address", "site"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "asset_id",
            ("asset_id", "asset_ref", "asset_reference", "asset"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "status",
            ("status", "job_status", "outcome", "result", "state"),
            _R.OPTIONAL,
        ),
        FieldSpec(
            "notes",
            ("notes", "note", "work_notes", "comments", "remarks", "description",
             "details"),
            _R.OPTIONAL,
        ),
        FieldSpec("latitude", ("latitude", "lat", "y_coord", "y"), _R.OPTIONAL),
        FieldSpec(
            "longitude",
            ("longitude", "lng", "lon", "long", "x_coord", "x"),
            _R.OPTIONAL,
        ),
    ),
)


SCHEMAS: Final[Mapping[SourceType, SourceSchema]] = {
    SourceType.REPORTS: REPORTS_SCHEMA,
    SourceType.ASSETS: ASSETS_SCHEMA,
    SourceType.JOBS: JOBS_SCHEMA,
}


def get_schema(source_type: SourceType) -> SourceSchema:
    return SCHEMAS[source_type]


def iter_schemas() -> Iterable[SourceSchema]:
    return SCHEMAS.values()

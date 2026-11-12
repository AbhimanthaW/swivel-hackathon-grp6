"""Column resolution, source-type detection and shared parse helpers.

Policy lives here: what counts as usable, what is merely a warning, and how a
file is identified when the caller does not say.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .mappings import Requirement, SourceSchema, get_schema, iter_schemas
from .models import (
    Coordinates,
    Issue,
    IssueCode,
    Severity,
    SourceType,
)
from .normalizers import normalize_enum_value, parse_float

#: Approximate bounding box for the Muthuwella / Colombo service area. Points
#: outside are kept but flagged - see docs/ASSUMPTIONS.md.
DEFAULT_BOUNDING_BOX: tuple[float, float, float, float] = (6.75, 79.75, 7.00, 80.00)

#: Detection confidence below this means "I do not recognise this file".
DETECTION_MIN_SCORE = 0.45
#: Two schemas within this margin means "I cannot tell these apart".
DETECTION_MIN_MARGIN = 0.15


def warn(code: IssueCode, message: str, **kwargs: object) -> Issue:
    return Issue(code=code, severity=Severity.WARNING, message=message, **kwargs)  # type: ignore[arg-type]


def error(code: IssueCode, message: str, **kwargs: object) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=message, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Column resolution
# --------------------------------------------------------------------------


@dataclass
class ColumnMap:
    """Resolution of raw headers against one schema."""

    schema: SourceSchema
    mapping: dict[str, str] = field(default_factory=dict)
    unused: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    missing_semantic: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.missing_required


def resolve_columns(headers: Sequence[str], schema: SourceSchema) -> ColumnMap:
    """Map raw headers onto canonical field names.

    Reordered, renamed, extra and missing columns are all tolerated. When two
    headers claim the same canonical field, the one whose alias has the higher
    preference (declared earlier in the ``FieldSpec``) wins and a warning is
    recorded.
    """
    result = ColumnMap(schema=schema)
    best: dict[str, tuple[int, str]] = {}  # canonical -> (priority, header)

    for header in headers:
        hit = schema.match(header)
        if hit is None:
            result.unused.append(header)
            continue
        canonical, priority = hit
        incumbent = best.get(canonical)
        if incumbent is None:
            best[canonical] = (priority, header)
            continue
        winner, loser = (
            (header, incumbent[1]) if priority < incumbent[0] else (incumbent[1], header)
        )
        best[canonical] = (min(priority, incumbent[0]), winner)
        result.unused.append(loser)
        result.issues.append(
            warn(
                IssueCode.AMBIGUOUS_COLUMN,
                f"Columns {incumbent[1]!r} and {header!r} both map to "
                f"{canonical!r}; using {winner!r}.",
                field=canonical,
            )
        )

    result.mapping = {name: header for name, (_, header) in best.items()}

    for spec in schema.fields:
        if spec.name in result.mapping:
            continue
        if spec.requirement is Requirement.REQUIRED:
            result.missing_required.append(spec.name)
        elif spec.requirement is Requirement.SEMANTIC:
            result.missing_semantic.append(spec.name)
        else:
            result.missing_optional.append(spec.name)

    for name in result.missing_required:
        result.issues.append(
            error(
                IssueCode.MISSING_REQUIRED_COLUMN,
                f"Required field {name!r} could not be mapped. Expected one of: "
                f"{', '.join(schema.spec(name).aliases)}.",
                field=name,
            )
        )
    for name in result.missing_semantic:
        result.issues.append(
            warn(
                IssueCode.MISSING_SEMANTIC_COLUMN,
                f"No column found for {name!r}; downstream analysis will be degraded. "
                f"Expected one of: {', '.join(schema.spec(name).aliases)}.",
                field=name,
            )
        )
    for header in result.unused:
        result.issues.append(
            warn(
                IssueCode.UNUSED_COLUMN,
                f"Column {header!r} was not recognised and will be ignored.",
                field=header,
            )
        )
    return result


# --------------------------------------------------------------------------
# Source type detection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectionCandidate:
    source_type: SourceType
    score: float
    matched_signature: tuple[str, ...]
    missing_required: tuple[str, ...]


@dataclass(frozen=True)
class DetectionResult:
    source_type: SourceType | None
    confidence: float
    candidates: tuple[DetectionCandidate, ...]
    reason: str

    @property
    def is_confident(self) -> bool:
        return self.source_type is not None


def _score(headers: Sequence[str], schema: SourceSchema) -> DetectionCandidate:
    column_map = resolve_columns(headers, schema)
    matched_signature = tuple(
        name for name in schema.signature_fields if name in column_map.mapping
    )
    signature_ratio = len(matched_signature) / len(schema.signature_fields)
    coverage = (len(column_map.mapping) / len(headers)) if headers else 0.0
    score = 0.7 * signature_ratio + 0.3 * min(coverage, 1.0)
    if column_map.missing_required:
        score *= 0.5
    return DetectionCandidate(
        source_type=schema.source_type,
        score=round(score, 4),
        matched_signature=matched_signature,
        missing_required=tuple(column_map.missing_required),
    )


def detect_source_type(headers: Sequence[str]) -> DetectionResult:
    """Identify a file from its headers alone.

    A convenience only - the caller should pass ``source_type`` explicitly
    whenever it knows. Filenames are deliberately not consulted.
    """
    if not headers:
        return DetectionResult(None, 0.0, (), "no header row")

    candidates = tuple(
        sorted(
            (_score(headers, schema) for schema in iter_schemas()),
            key=lambda c: c.score,
            reverse=True,
        )
    )
    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None

    if top.score < DETECTION_MIN_SCORE:
        return DetectionResult(
            None,
            top.score,
            candidates,
            "no schema matched these columns with sufficient confidence",
        )
    if runner_up and (top.score - runner_up.score) < DETECTION_MIN_MARGIN:
        return DetectionResult(
            None,
            top.score,
            candidates,
            f"columns match {top.source_type.value} and {runner_up.source_type.value} "
            "equally well; supply source_type explicitly",
        )
    return DetectionResult(top.source_type, top.score, candidates, "matched by columns")


def check_declared_source_type(
    headers: Sequence[str], declared: SourceType
) -> list[Issue]:
    """Warn/err when the caller's declared type disagrees with the columns.

    This is what turns "user uploaded assets.csv into the reports slot" into a
    useful message instead of 184 rejected rows.
    """
    issues: list[Issue] = []
    detection = detect_source_type(headers)
    declared_map = resolve_columns(headers, get_schema(declared))

    if detection.source_type is not None and detection.source_type is not declared:
        severity_issue = error if not declared_map.usable else warn
        issues.append(
            severity_issue(
                IssueCode.SOURCE_TYPE_MISMATCH,
                f"File was supplied as {declared.value!r} but its columns look like "
                f"{detection.source_type.value!r} "
                f"(confidence {detection.confidence:.0%}). Check the upload slot.",
            )
        )
    elif detection.source_type is None and not declared_map.usable:
        issues.append(
            warn(
                IssueCode.SOURCE_TYPE_UNDETECTED,
                f"Could not confirm this is a {declared.value!r} export: "
                f"{detection.reason}.",
            )
        )
    return issues


# --------------------------------------------------------------------------
# Shared row-level parsing helpers
# --------------------------------------------------------------------------


def resolve_enum(
    raw: str | None,
    aliases: Mapping[str, object],
    *,
    field_name: str,
    row_number: int,
    source_id: str | None = None,
) -> tuple[object | None, Issue | None]:
    """Look a value up in an alias table, reporting unknowns instead of guessing."""
    key = normalize_enum_value(raw)
    if key is None:
        return None, None
    value = aliases.get(key)
    if value is None:
        return None, warn(
            IssueCode.UNKNOWN_ENUM_VALUE,
            f"Unrecognised {field_name} value {raw!r}; keeping the raw value.",
            row_number=row_number,
            field=field_name,
            source_id=source_id,
        )
    return value, None


def parse_coordinates(
    raw_lat: str | None,
    raw_lon: str | None,
    *,
    row_number: int,
    source_id: str | None = None,
    bounding_box: tuple[float, float, float, float] = DEFAULT_BOUNDING_BOX,
) -> tuple[Coordinates | None, list[Issue]]:
    """Parse a lat/lon pair, rejecting sentinels and flagging outliers.

    ``0, 0`` is treated as "not supplied" - it is a null sentinel, not a point
    in the Gulf of Guinea. Points outside the service area are *kept* but
    flagged, because discarding them would destroy information.
    """
    issues: list[Issue] = []
    lat, lat_error = parse_float(raw_lat)
    lon, lon_error = parse_float(raw_lon)

    for value_error, field_name in ((lat_error, "latitude"), (lon_error, "longitude")):
        if value_error:
            issues.append(
                warn(
                    IssueCode.INVALID_COORDINATE,
                    f"{field_name}: {value_error}",
                    row_number=row_number,
                    field=field_name,
                    source_id=source_id,
                )
            )

    if lat is None and lon is None:
        return None, issues
    if lat is None or lon is None:
        issues.append(
            warn(
                IssueCode.PARTIAL_COORDINATE,
                "Only one of latitude/longitude was supplied; ignoring both.",
                row_number=row_number,
                source_id=source_id,
            )
        )
        return None, issues

    if lat == 0.0 and lon == 0.0:
        issues.append(
            warn(
                IssueCode.INVALID_COORDINATE,
                "Coordinates are 0,0 - treating as not supplied.",
                row_number=row_number,
                source_id=source_id,
            )
        )
        return None, issues

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        issues.append(
            warn(
                IssueCode.INVALID_COORDINATE,
                f"Coordinates {lat},{lon} are not a valid point; ignoring.",
                row_number=row_number,
                source_id=source_id,
            )
        )
        return None, issues

    min_lat, min_lon, max_lat, max_lon = bounding_box
    in_area = min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
    if not in_area:
        issues.append(
            warn(
                IssueCode.COORDINATE_OUT_OF_AREA,
                f"Coordinates {lat},{lon} fall outside the council service area; "
                "retained but flagged.",
                row_number=row_number,
                source_id=source_id,
            )
        )
    return Coordinates(latitude=lat, longitude=lon, in_expected_area=in_area), issues

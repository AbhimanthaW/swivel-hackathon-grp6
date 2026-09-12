"""Council asset / location parsing.

The export has mixed grain: one row per asset, with road-level attributes
repeated on every row. This module produces both views -

  * :class:`CouncilAsset` - one per source row, the faithful representation;
  * :class:`RoadRecord`   - the de-duplicated road entity that later location
    enrichment and priority scoring actually need.

Different asset types do not share identical fields, so almost everything
except the id is optional.
"""

from __future__ import annotations

import re
from typing import Iterable

from .mappings import ASSET_TYPE_ALIASES, ROAD_CLASS_ALIASES
from .models import (
    AssetType,
    CouncilAsset,
    DataQualityReport,
    IngestionResult,
    IssueCode,
    RawLocation,
    RoadContext,
    RoadRecord,
    SourceType,
)
from .normalizers import (
    clean_line,
    normalize_location_hint,
    parse_int,
)
from .pipeline import ParseContext, SourceParser, ingest_source
from .reader import RowView, TableSource
from .validation import parse_coordinates, resolve_enum, warn

_ALIAS_SPLIT = re.compile(r"\s*[;|/]\s*")


def _split_aliases(value: str | None) -> tuple[str, ...]:
    """Split an alternative-names cell.

    Commas are NOT treated as separators: council alias values legitimately
    contain them (``"R A de Mel Mawatha, Colombo 3"``). Semicolon, pipe and
    slash are.
    """
    text = clean_line(value)
    if text is None:
        return ()
    parts = (clean_line(p) for p in _ALIAS_SPLIT.split(text))
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return tuple(seen)


def parse_asset_row(view: RowView, ctx: ParseContext) -> CouncilAsset | None:
    asset_id = clean_line(view["asset_id"])
    if asset_id is None:
        ctx.count("missing_ids")
        ctx.add(
            warn(
                IssueCode.MISSING_ID,
                "Asset has no id; an internal id was assigned.",
                row_number=view.row_number,
                field="asset_id",
            )
        )

    asset_type_raw = clean_line(view["asset_type"])
    asset_type, type_issue = resolve_enum(
        asset_type_raw,
        ASSET_TYPE_ALIASES,
        field_name="asset_type",
        row_number=view.row_number,
        source_id=asset_id,
    )
    ctx.add(type_issue)
    if type_issue is not None and asset_type_raw:
        ctx.note_unknown("asset_type", asset_type_raw)

    road_class_raw = clean_line(view["road_class"])
    road_class, class_issue = resolve_enum(
        road_class_raw,
        ROAD_CLASS_ALIASES,
        field_name="road_class",
        row_number=view.row_number,
        source_id=asset_id,
    )
    ctx.add(class_issue)
    if class_issue is not None and road_class_raw:
        ctx.note_unknown("road_class", road_class_raw)

    distance, distance_error = parse_int(view["facility_distance_m"])
    if distance_error:
        ctx.add(
            warn(
                IssueCode.UNPARSEABLE_NUMBER,
                f"facility_distance_m: {distance_error}",
                row_number=view.row_number,
                field="facility_distance_m",
                source_id=asset_id,
            )
        )

    coordinates, coord_issues = parse_coordinates(
        view["latitude"],
        view["longitude"],
        row_number=view.row_number,
        source_id=asset_id,
    )
    ctx.add_all(coord_issues)
    if coord_issues:
        ctx.count("invalid_coordinates")
    if coordinates is not None:
        ctx.count("records_with_coordinates")

    road_name = clean_line(view["road_name"])
    address = clean_line(view["address"])
    location_text = address or road_name
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
                "Asset has no road, address or coordinates; it cannot be located.",
                row_number=view.row_number,
                source_id=asset_id,
            )
        )

    road = RoadContext(
        name=road_name,
        name_key=normalize_location_hint(road_name),
        aliases=_split_aliases(view["road_aliases"]),
        road_class=road_class,  # type: ignore[arg-type]
        road_class_raw=road_class_raw,
        ward=clean_line(view["ward"]),
        nearest_facility=clean_line(view["nearest_facility"]),
        facility_distance_m=distance,
    )

    return CouncilAsset(
        ingest_id=ctx.mint_id(view.row_number, asset_id),
        source=ctx.metadata(view.row_number),
        raw=dict(view.raw),
        asset_id=asset_id,
        asset_type=asset_type or AssetType.OTHER,  # type: ignore[arg-type]
        asset_type_raw=asset_type_raw,
        name=clean_line(view["asset_name"]),
        road=road,
        location=location,
        owner=clean_line(view["owner"]),
    )


def build_road_directory(assets: Iterable[CouncilAsset]) -> list[RoadRecord]:
    """Collapse asset rows into one record per road.

    Single pass, keyed on the normalized road name. Conflicting road-level
    values across rows are resolved by first-non-null, which matches how the
    export repeats them. This is grouping, not location resolution - no
    fuzzy matching happens here.
    """
    grouped: dict[str, dict[str, object]] = {}

    for asset in assets:
        key = asset.road.name_key
        if not key or not asset.road.name:
            continue
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "name": asset.road.name,
                "name_key": key,
                "aliases": [],
                "road_class": asset.road.road_class,
                "ward": asset.road.ward,
                "nearest_facility": asset.road.nearest_facility,
                "facility_distance_m": asset.road.facility_distance_m,
                "asset_ids": [],
                "asset_types": [],
            }
            grouped[key] = entry
        for name in ("road_class", "ward", "nearest_facility", "facility_distance_m"):
            if entry[name] is None:
                entry[name] = getattr(asset.road, name)
        aliases: list[str] = entry["aliases"]  # type: ignore[assignment]
        for alias in asset.road.aliases:
            if alias not in aliases:
                aliases.append(alias)
        if asset.asset_id:
            entry["asset_ids"].append(asset.asset_id)  # type: ignore[union-attr]
        types: list[AssetType] = entry["asset_types"]  # type: ignore[assignment]
        if asset.asset_type not in types:
            types.append(asset.asset_type)

    return [
        RoadRecord(
            name=entry["name"],  # type: ignore[arg-type]
            name_key=entry["name_key"],  # type: ignore[arg-type]
            aliases=tuple(entry["aliases"]),  # type: ignore[arg-type]
            road_class=entry["road_class"],  # type: ignore[arg-type]
            ward=entry["ward"],  # type: ignore[arg-type]
            nearest_facility=entry["nearest_facility"],  # type: ignore[arg-type]
            facility_distance_m=entry["facility_distance_m"],  # type: ignore[arg-type]
            asset_ids=tuple(entry["asset_ids"]),  # type: ignore[arg-type]
            asset_types=tuple(entry["asset_types"]),  # type: ignore[arg-type]
        )
        for entry in grouped.values()
    ]


def _asset_quality(
    records: list[CouncilAsset], quality: DataQualityReport, ctx: ParseContext
) -> None:
    roads = build_road_directory(records)
    type_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {}
    wards: list[str] = []
    for record in records:
        key = record.asset_type.value
        type_counts[key] = type_counts.get(key, 0) + 1
    for road in roads:
        name = road.road_class.value if road.road_class else "unclassified"
        class_counts[name] = class_counts.get(name, 0) + 1
        if road.ward and road.ward not in wards:
            wards.append(road.ward)
    quality.extras.update(
        {
            "distinct_roads": len(roads),
            "roads_with_aliases": sum(1 for r in roads if r.aliases),
            "roads_with_facility": sum(1 for r in roads if r.nearest_facility),
            "asset_type_counts": type_counts,
            "road_class_counts": class_counts,
            "wards": sorted(wards),
        }
    )


ASSETS_PARSER: SourceParser[CouncilAsset] = SourceParser(
    source_type=SourceType.ASSETS,
    parse_row=parse_asset_row,
    source_id_attr="asset_id",
    quality_hook=_asset_quality,
)


def ingest_assets(
    source: TableSource,
    *,
    source_name: str | None = None,
    verify_source_type: bool = True,
) -> IngestionResult[CouncilAsset]:
    """Ingest a council asset export."""
    return ingest_source(
        source,
        ASSETS_PARSER,
        source_name=source_name,
        verify_source_type=verify_source_type,
    )

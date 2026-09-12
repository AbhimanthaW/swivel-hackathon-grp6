"""Asset export parsing and the derived road directory."""

from __future__ import annotations

from ingestion import (
    AssetType,
    IssueCode,
    RoadClass,
    build_road_directory,
    ingest_assets,
)


def test_valid_asset_csv(assets_csv: str) -> None:
    result = ingest_assets(assets_csv, source_name="assets.csv")

    assert result.ok
    assert result.accepted_count == 4
    first = result.records[0]
    assert first.asset_id == "MMC-STR-0001"
    assert first.asset_type is AssetType.STREETLIGHT
    assert first.road.name == "Temple Lane"
    assert first.road.road_class is RoadClass.RESIDENTIAL
    assert first.road.ward == "Havelock Town"
    assert first.road.aliases == ("the road by the temple",)

    facility_asset = result.records[2]
    assert facility_asset.road.nearest_facility == "Central College"
    assert facility_asset.road.facility_distance_m == 180


def test_asset_types_do_not_share_all_fields() -> None:
    """A row for one asset type may omit fields another type provides."""
    csv = (
        "asset_id,asset_type,road_name,lamp_wattage,drain_diameter_mm\n"
        "MMC-STR-1,streetlight,Galle Road,70,\n"
        "MMC-DRA-1,drain,Galle Road,,300\n"
    )
    result = ingest_assets(csv)

    assert result.accepted_count == 2
    assert result.quality.unused_columns == ["lamp_wattage", "drain_diameter_mm"]
    assert result.records[0].asset_type is AssetType.STREETLIGHT
    assert result.records[1].asset_type is AssetType.DRAIN


def test_road_directory_collapses_mixed_grain(assets_csv: str) -> None:
    """The export repeats road attributes per asset; roads must de-duplicate."""
    result = ingest_assets(assets_csv)
    roads = build_road_directory(result.records)

    assert len(roads) == 3
    temple = next(r for r in roads if r.name == "Temple Lane")
    assert temple.asset_ids == ("MMC-STR-0001", "MMC-DRA-0001")
    assert set(temple.asset_types) == {AssetType.STREETLIGHT, AssetType.DRAIN}
    assert temple.aliases == ("the road by the temple",)
    assert temple.search_terms == ("Temple Lane", "the road by the temple")


def test_road_directory_backfills_sparse_road_attributes() -> None:
    csv = (
        "asset_id,asset_type,road_name,road_class,ward,nearest_facility,facility_distance_m\n"
        "A-1,drain,Galle Road,,,,\n"
        "A-2,streetlight,Galle Road,main road,Kollupitiya,St Marys,210\n"
    )
    roads = build_road_directory(ingest_assets(csv).records)

    assert len(roads) == 1
    assert roads[0].road_class is RoadClass.MAIN_ROAD
    assert roads[0].ward == "Kollupitiya"
    assert roads[0].facility_distance_m == 210


def test_road_name_key_is_abbreviation_insensitive() -> None:
    csv = "asset_id,asset_type,road_name\nA-1,drain,Temple Ln\nA-2,drain,Temple Lane\n"
    roads = build_road_directory(ingest_assets(csv).records)

    assert len(roads) == 1, "Temple Ln and Temple Lane are the same road"
    assert roads[0].name_key == "temple lane"


def test_multiple_aliases_are_split_on_semicolons_not_commas() -> None:
    csv = (
        "asset_id,asset_type,road_name,also_known_as\n"
        'A-1,drain,Duplication Road,"R A de Mel Mawatha; the duplication"\n'
        'A-2,drain,Baseline Road,"Elvitigala Mawatha, Colombo 8"\n'
    )
    records = ingest_assets(csv).records

    assert records[0].road.aliases == ("R A de Mel Mawatha", "the duplication")
    assert records[1].road.aliases == ("Elvitigala Mawatha, Colombo 8",)


def test_unknown_asset_type_keeps_raw_value() -> None:
    csv = "asset_id,asset_type,road_name\nA-1,pelican crossing,Galle Road\n"
    result = ingest_assets(csv)

    assert result.records[0].asset_type is AssetType.OTHER
    assert result.records[0].asset_type_raw == "pelican crossing"
    assert "asset_type" in result.quality.unknown_values


def test_unparseable_facility_distance_is_warned() -> None:
    csv = (
        "asset_id,asset_type,road_name,facility_distance_m\n"
        "A-1,drain,Galle Road,about 200m\n"
    )
    result = ingest_assets(csv)

    assert result.accepted_count == 1
    assert result.records[0].road.facility_distance_m is None
    assert any(i.code is IssueCode.UNPARSEABLE_NUMBER for i in result.warnings)


def test_optional_coordinates_when_the_export_gains_them() -> None:
    csv = (
        "asset_id,asset_type,road_name,lat,lng\n"
        "A-1,drain,Galle Road,6.9,79.86\n"
        "A-2,drain,Galle Road,0,0\n"
        "A-3,drain,Galle Road,,\n"
    )
    result = ingest_assets(csv)

    assert result.records[0].location.coordinates.latitude == 6.9
    assert result.records[1].location.coordinates is None, "0,0 is a null sentinel"
    assert result.records[2].location.coordinates is None
    assert result.quality.records_with_coordinates == 1


def test_asset_quality_extras(assets_csv: str) -> None:
    quality = ingest_assets(assets_csv).quality

    assert quality.extras["distinct_roads"] == 3
    assert quality.extras["roads_with_aliases"] == 1
    assert quality.extras["roads_with_facility"] == 1
    assert quality.extras["wards"] == ["Havelock Town", "Thimbirigasyaya"]

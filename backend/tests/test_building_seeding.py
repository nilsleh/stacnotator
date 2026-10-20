"""Seeding the local building campaigns: imagery plan, references, tiler registration."""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from shapely.geometry import box

from src.buildings import seeding

CELL = box(10.0, 20.0, 10.01, 20.01)


def _dataset(**extra):
    return {
        "key": "sn_test",
        "name": "Test dataset",
        "dataset_key": "spacenet9",
        "default_zoom": 18,
        "tiler_params": "bidx=1&bidx=2&bidx=3&rescale=0,1400",
        "campaign_bbox": {"west": 9.9, "south": 19.9, "east": 10.2, "north": 20.2},
        "chips": [
            {
                "chip_id": "a",
                "tif": "t/a.tif",
                "geojson": "t/a.geojson",
                "bbox": [10.0, 20.0, 10.01, 20.01],
                "split": "val",
            },
            {
                "chip_id": "b",
                "tif": "t/b.tif",
                "geojson": "t/b.geojson",
                "bbox": [10.1, 20.1, 10.11, 20.11],
                "split": "val",
            },
        ],
        **extra,
    }


def _fc(*geoms, crs=None):
    fc = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": g} for g in geoms],
    }
    if crs:
        fc["crs"] = {"type": "name", "properties": {"name": crs}}
    return fc


def _square(x, y, d, z=None):
    ring = [[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]
    if z is not None:
        ring = [[*p, z] for p in ring]
    return {"type": "Polygon", "coordinates": [ring]}


def test_tiler_params_become_band_and_stretch_viz_params():
    assert seeding.parse_tiler_params("bidx=5&bidx=3&bidx=2&rescale=0,1200") == {
        "assets": ["data"],
        "bidx": [5, 3, 2],
        "rescale": "0,1200",
    }


def test_one_rescale_per_band_reaches_the_tile_url():
    from src.tilers.providers import build_viz_query_string

    viz = seeding.parse_tiler_params(
        "bidx=1&bidx=2&bidx=3&rescale=0,490&rescale=0,640&rescale=0,500"
    )
    assert viz["rescale"] == ["0,490", "0,640", "0,500"]
    query = build_viz_query_string(viz)
    assert [p for p in query.split("&") if p.startswith("rescale")] == [
        "rescale=0%2C490",
        "rescale=0%2C640",
        "rescale=0%2C500",
    ]


@pytest.mark.parametrize(
    "params",
    ["bidx=1&colormap_name=viridis", "rescale=5,1", "bidx=x", "bidx=1&rescale=0,1&rescale=0,2"],
)
def test_unsupported_or_malformed_tiler_params_are_refused(params):
    with pytest.raises(ValueError):
        seeding.parse_tiler_params(params)


def test_chips_without_slices_are_mosaicked_into_one_slice():
    (source,) = seeding.plan_sources(_dataset())
    assert source.name == "SPACENET9 Primary"
    assert source.viz_params["bidx"] == [1, 2, 3]
    (only,) = source.slices
    assert only.tifs == ("t/a.tif", "t/b.tif")


def test_strict_chip_imagery_never_mosaics_another_acquisition():
    (source,) = seeding.plan_sources(_dataset(scene_imagery="per_chip"))
    assert [sl.tifs for sl in source.slices] == [("t/a.tif",), ("t/b.tif",)]
    assert [sl.name for sl in source.slices] == ["Scene 1", "Scene 2"]


@pytest.mark.parametrize("params", ["rescale=0,nan", "rescale=-inf,1", "bidx=0", "nodata=inf"])
def test_invalid_render_ranges_are_rejected(params):
    with pytest.raises(ValueError):
        seeding.parse_tiler_params(params)


def test_nodata_override_reuses_existing_tile_url_builder():
    from src.tilers.providers import build_viz_query_string

    assert "nodata=0" in build_viz_query_string(seeding.parse_tiler_params("bidx=1&nodata=0"))


def test_dataset_slices_become_time_slices_and_secondary_becomes_a_source():
    ds = _dataset(
        slices=[
            {
                "name": "Nadir 7",
                "start_date": "2009-12-07",
                "end_date": "2009-12-08",
                "tif": "t/n7.tif",
            },
            {
                "name": "Nadir 25",
                "start_date": "2009-12-15",
                "end_date": "2009-12-16",
                "tif": "t/n25.tif",
            },
        ],
        secondary_source={"name": "MS", "tiler_params": "bidx=5&bidx=3&bidx=2&rescale=0,1200"},
    )
    ds["chips"][0]["secondary_tif"] = "t/a_ms.tif"
    primary, secondary = seeding.plan_sources(ds)
    assert [(s.name, s.tifs) for s in primary.slices] == [
        ("Nadir 7", ("t/n7.tif",)),
        ("Nadir 25", ("t/n25.tif",)),
    ]
    assert secondary.name == "MS"
    assert secondary.slices[0].tifs == ("t/a_ms.tif",)
    assert secondary.viz_params["bidx"] == [5, 3, 2]


def test_secondary_source_without_secondary_chips_is_refused():
    with pytest.raises(ValueError, match="secondary_tif"):
        seeding.plan_sources(_dataset(secondary_source={"name": "MS", "tiler_params": "bidx=1"}))


def test_references_are_2d_wgs84_and_clipped_to_the_cell():
    inside = _square(10.002, 20.002, 0.001, z=0.0)
    crossing = _square(10.009, 20.005, 0.004)
    outside = _square(11.0, 21.0, 0.001)
    refs = seeding.normalize_references(_fc(inside, crossing, outside), CELL)
    assert len(refs) == 2
    assert all(not r.has_z for r in refs)
    assert all(CELL.buffer(1e-12).contains(r) for r in refs)
    assert refs[1].bounds[2] == pytest.approx(10.01)


def test_projected_references_are_reprojected():
    # EPSG:3857 square near (10.005, 20.005), inside the cell once reprojected.
    from pyproj import Transformer

    x, y = Transformer.from_crs(4326, 3857, always_xy=True).transform(10.005, 20.005)
    refs = seeding.normalize_references(
        _fc(_square(x, y, 50), crs="urn:ogc:def:crs:EPSG::3857"), CELL
    )
    (ref,) = refs
    assert ref.bounds[0] == pytest.approx(10.005, abs=1e-6)
    assert ref.bounds[1] == pytest.approx(20.005, abs=1e-6)


def test_scenes_carry_only_their_split():
    geojsons = {"t/a.geojson": _fc(_square(10.002, 20.002, 0.001)), "t/b.geojson": _fc()}
    a, b = seeding.plan_scenes(_dataset(), geojsons)
    # Readable by every member through the predictions export: nothing about the answer.
    assert a.raw_source_data == b.raw_source_data == {"split": "val"}
    assert (len(a.references), len(b.references)) == (1, 0)
    assert a.cell.bounds == (10.0, 20.0, 10.01, 20.01)


def test_polygon_policy_preserves_holes_and_rejects_multipart_references():
    ds = _dataset(geometry_policy="polygon-v1")
    outer = _square(10.002, 20.002, 0.005)
    outer["coordinates"].append(_square(10.003, 20.003, 0.001)["coordinates"][0])
    geojsons = {"t/a.geojson": _fc(outer), "t/b.geojson": _fc()}
    assert len(seeding.plan_scenes(ds, geojsons)[0].references[0].interiors) == 1
    geojsons["t/a.geojson"] = _fc(
        {
            "type": "MultiPolygon",
            "coordinates": [
                _square(10.002, 20.002, 0.001)["coordinates"],
                _square(10.006, 20.006, 0.001)["coordinates"],
            ],
        }
    )
    with pytest.raises(ValueError, match="multipart"):
        seeding.plan_scenes(ds, geojsons)
    assert len(seeding.plan_scenes(_dataset(), geojsons)[0].references[0].geoms) == 2


def test_item_id_matches_the_tilers_register_cog():
    # stacnotator-tiler: "cm-" + sha1(cog_url)[:16]
    url = "/data/cogs/curated/sn1_rio/img1011.tif"
    expected = "cm-" + hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()[:16]
    assert seeding.tiler_cog_item_id(url) == expected


def test_one_chip_uses_its_own_search_and_several_get_a_mosaic(monkeypatch):
    registered = []
    monkeypatch.setattr(
        seeding,
        "register_cog_on_tiler",
        lambda tiler, url, scope, timeout: registered.append((url, scope)) or f"s-{url[-5:]}",
    )
    mosaic = MagicMock(return_value="mosaic")
    monkeypatch.setattr(seeding, "register_on_tiler", mosaic)
    registrar = seeding.TilerRegistrar("hosted", SimpleNamespace(url="http://t"), "42", "/c")

    assert registrar.search(("x/a.tif",)) == "s-a.tif"
    mosaic.assert_not_called()

    assert registrar.search(("x/a.tif", "x/b.tif")) == "mosaic"
    body, scope = mosaic.call_args.args[1:]
    assert scope == "42"
    assert body == {
        "collections": ["custom-maps"],
        "ids": [seeding.tiler_cog_item_id("/c/x/a.tif"), seeding.tiler_cog_item_id("/c/x/b.tif")],
    }
    assert {scope for _, scope in registered} == {"42"}


def test_tile_urls_are_stamped_with_the_tiler_and_search():
    registrar = seeding.TilerRegistrar(
        "hosted", SimpleNamespace(url="http://t", internal_url=None), "1", "/c"
    )
    url = registrar.tile_url("abc", {"assets": ["data"], "bidx": [1, 2, 3], "rescale": "0,255"})
    assert url.startswith("http://t/searches/abc/tiles/")
    row = SimpleNamespace(tile_url=url, tile_provider=None, mosaic_id=None)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [row]
    registrar.stamp(db, 1)
    assert (row.tile_provider, row.mosaic_id) == ("hosted", "abc")


def test_reset_needs_the_confirmation_phrase():
    with pytest.raises(ValueError, match="DELETE-ANNOTATIONS"):
        seeding.seed(MagicMock(), {"datasets": []}, project_name="p", user_uid="u", reset=True)


def test_existing_campaigns_are_reused_unchanged(monkeypatch):
    monkeypatch.setattr(
        seeding,
        "get_settings",
        lambda: SimpleNamespace(TILER_LOCAL_COG_DIR="/data/cogs", DEFAULT_TILER="hosted"),
    )
    monkeypatch.setattr(seeding, "resolve_tiler", lambda name: SimpleNamespace(url="http://t"))
    monkeypatch.setattr(seeding, "_campaigns_named", lambda db, pid, name: [SimpleNamespace(id=7)])
    create = MagicMock()
    monkeypatch.setattr(seeding, "_create_campaign", create)
    delete = MagicMock()
    monkeypatch.setattr(seeding, "delete_campaign", delete)
    db = MagicMock()
    db.scalar.side_effect = [SimpleNamespace(id=2), SimpleNamespace(id="user")]
    payload = {"datasets": [_dataset()], "geojsons": {"t/a.geojson": _fc(), "t/b.geojson": _fc()}}

    (result,) = seeding.seed(db, payload, project_name="p", user_uid="u")

    assert result == seeding.SeedResult("sn_test", 7, created=False)
    create.assert_not_called()
    delete.assert_not_called()

import random

import numpy as np
import pytest
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_origin

import build_ramp_manifest
from build_ramp_manifest import build_city, inspect_chip


def raster(tmp_path, *, empty=False):
    path = tmp_path / "chip.tif"
    data = np.full((3, 10, 10), 65535, dtype="uint16")
    if not empty:
        for band in range(3):
            data[band, 2:8, 2:8] = np.arange(36).reshape(6, 6) + 100 + band * 100
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=3,
        dtype="uint16",
        crs="EPSG:32630",
        transform=from_origin(700000, 600000, 0.3, 0.3),
        nodata=65535,
    ) as dst:
        dst.write(data)
        dst.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue)
    return path


def test_stretch_excludes_embedded_nodata_and_retains_alignment(tmp_path):
    info = inspect_chip(raster(tmp_path))
    assert info["highs"] == pytest.approx([134.3, 234.3, 334.3])
    assert info["crs"] == "EPSG:32630"
    assert info["rgb_bands"] == [1, 2, 3]
    assert info["nodata"] == [65535, 65535, 65535]
    assert info["transform"] == [0.3, 0, 700000, 0, -0.3, 600000]
    assert len(info["footprint"]["coordinates"][0]) == 65


def test_empty_raster_and_wrong_rgb_order_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="no usable dynamic range"):
        inspect_chip(raster(tmp_path, empty=True))
    with pytest.raises(ValueError, match="RGB band order"):
        inspect_chip(raster(tmp_path), (3, 2, 1))


def test_ramp_manifest_declares_roof_semantics_and_pinned_chip(tmp_path, monkeypatch):
    city_dir = tmp_path / "ramp_accra_ghana"
    (city_dir / "source").mkdir(parents=True)
    (city_dir / "labels").mkdir()
    raster(city_dir / "source")
    (city_dir / "labels" / "chip.geojson").write_text(
        '{"features": []}', encoding="utf-8"
    )
    monkeypatch.setattr(build_ramp_manifest, "RAMP_DIR", tmp_path)

    manifest = build_city(
        "accra_ghana",
        {"scenes_per_city": 1, "empty_fraction": 1, "splits": {"test": 1}},
        random.Random(0),
    )

    assert manifest["footprint_semantics"] == "roof"
    assert manifest["geometry_policy"] == "polygon-v1"
    assert manifest["scene_imagery"] == "per_chip"
    assert manifest["chips"][0]["chip_id"] == "chip"
    assert (
        manifest["chips"][0]["footprint"]
        == inspect_chip(city_dir / "source" / "chip.tif")["footprint"]
    )

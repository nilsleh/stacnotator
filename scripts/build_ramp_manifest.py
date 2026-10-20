"""Build a curated building-campaign manifest from the downloaded RAMP dataset.

One campaign per configured city, one scene per RAMP chip (256 x 256 px at ~30 cm).
Chips are sampled reproducibly and stratified by building count: a share of empty chips,
the rest spread evenly over sparse, medium and dense ones. The manifest is written next to
the data, so the seeder resolves chip paths relative to it:

    python scripts/build_ramp_manifest.py                      # scripts/ramp_campaigns.json
    python scripts/build_ramp_manifest.py --cities accra_ghana --scenes-per-city 50
    python scripts/seed_building_datasets.py --manifest data/datasets/ramp/stacnotator_manifest.json

Campaign names stay neutral (city only): agents see them, so they must not hint at what
the scenes hold. Needs rasterio on the host (pip install rasterio).
"""

import argparse
import json
import math
import pathlib
import random
import sys

import numpy as np
import rasterio
from rasterio.warp import transform

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAMP_DIR = ROOT / "data" / "datasets" / "ramp"
CONFIG = pathlib.Path(__file__).with_name("ramp_campaigns.json")
MANIFEST_NAME = "stacnotator_manifest.json"
DEFAULT_ZOOM = 19
DENSITY_BINS = 3
STRETCH_PERCENTILE = 98


def city_chips(city_dir: pathlib.Path) -> list[tuple[str, int]]:
    """(chip stem, building count) for every chip that has both imagery and labels."""
    chips = []
    for tif in sorted((city_dir / "source").glob("*.tif")):
        label = city_dir / "labels" / f"{tif.stem}.geojson"
        if label.is_file():
            features = (
                json.loads(label.read_text(encoding="utf-8")).get("features") or []
            )
            chips.append((tif.stem, sum(1 for f in features if f.get("geometry"))))
    return chips


def sample(
    chips: list[tuple[str, int]], n: int, empty_fraction: float, rng: random.Random
):
    """``n`` chips: ``empty_fraction`` of them empty, the rest even over density bins."""
    empty = [c for c in chips if c[1] == 0]
    built = sorted((c for c in chips if c[1] > 0), key=lambda c: (c[1], c[0]))
    n = min(n, len(chips))
    n_empty = min(len(empty), round(n * empty_fraction))
    k = len(built)
    bins = [
        built[i * k // DENSITY_BINS : (i + 1) * k // DENSITY_BINS]
        for i in range(DENSITY_BINS)
    ]
    picked = rng.sample(empty, n_empty)
    want = n - n_empty
    for i, b in enumerate(bins):
        share = want // DENSITY_BINS + (1 if i < want % DENSITY_BINS else 0)
        picked += rng.sample(b, min(share, len(b)))
    taken = set(picked)
    picked += rng.sample([c for c in chips if c not in taken], n - len(picked))
    return sorted(picked)


def assign_splits(stems: list[str], splits: dict[str, float], rng: random.Random):
    order = stems[:]
    rng.shuffle(order)
    names = list(splits)
    out, start = {}, 0
    for i, name in enumerate(names):
        end = (
            len(order)
            if i == len(names) - 1
            else start + round(splits[name] * len(order))
        )
        out.update({s: name for s in order[start:end]})
        start = end
    return out


def inspect_chip(tif: pathlib.Path, rgb_bands: tuple[int, ...] = (1, 2, 3)) -> dict:
    with rasterio.open(tif) as r:
        if r.crs is None or r.transform.is_degenerate:
            raise ValueError(f"{tif}: missing CRS or singular affine transform")
        if not all(math.isfinite(v) for v in r.transform):
            raise ValueError(f"{tif}: non-finite affine transform")
        if (
            len(rgb_bands) != 3
            or len(set(rgb_bands)) != 3
            or any(b < 1 or b > r.count for b in rgb_bands)
        ):
            raise ValueError(f"{tif}: RGB requires three distinct existing bands")
        for band, expected in zip(rgb_bands, ("red", "green", "blue"), strict=True):
            actual = r.colorinterp[band - 1].name
            if actual in ("red", "green", "blue") and actual != expected:
                raise ValueError(
                    f"{tif}: RGB band order disagrees with color interpretation"
                )
        dtypes = {r.dtypes[b - 1] for b in rgb_bands}
        if len(dtypes) != 1:
            raise ValueError(f"{tif}: RGB bands have different dtypes")
        dtype = next(iter(dtypes))
        highs = []
        for band in rgb_bands:
            values = r.read(band, masked=True).compressed()
            values = values[np.isfinite(values)]
            if values.size == 0 or values.max() <= values.min():
                raise ValueError(f"{tif}: RGB band {band} has no usable dynamic range")
            high = (
                255.0
                if dtype == "uint8"
                else float(np.percentile(values, STRETCH_PERCENTILE))
            )
            if not math.isfinite(high) or high <= 0:
                raise ValueError(f"{tif}: invalid RGB stretch")
            highs.append(high)
        corners = [(0, 0), (r.width, 0), (r.width, r.height), (0, r.height), (0, 0)]
        edge = [
            r.transform * (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            for a, b in zip(corners[:-1], corners[1:], strict=True)
            for t in np.linspace(0, 1, 17)[:-1]
        ]
        xs, ys = transform(r.crs, "EPSG:4326", *zip(*edge, strict=True))
        ring = [[x, y] for x, y in zip(xs, ys, strict=True)]
        ring.append(ring[0])
        return {
            "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "footprint": {"type": "Polygon", "coordinates": [ring]},
            "dtype": dtype,
            "highs": highs,
            "rgb_bands": list(rgb_bands),
            "crs": r.crs.to_string(),
            "transform": list(r.transform)[:6],
            "width": r.width,
            "height": r.height,
            "nodata": [
                v if v is None or math.isfinite(v) else str(v) for v in r.nodatavals
            ],
            "colorinterp": [c.name for c in r.colorinterp],
        }


def chip_raster(tif: pathlib.Path) -> tuple[list[float], str, list[float]]:
    """WGS84 bbox, dtype and mask-aware RGB bright-end stretch."""
    info = inspect_chip(tif)
    return info["bbox"], info["dtype"], info["highs"]


def city_title(city: str) -> str:
    *place, country = city.split("_")
    return f"{' '.join(place).title()}, {country.title()}"


def build_city(city: str, config: dict, rng: random.Random) -> dict:
    city_dir = RAMP_DIR / f"ramp_{city}"
    chips = city_chips(city_dir)
    if not chips:
        raise ValueError(f"{city}: no chips with labels in {city_dir}")
    picked = sample(chips, config["scenes_per_city"], config["empty_fraction"], rng)
    splits = assign_splits([s for s, _ in picked], config["splits"], rng)
    entries, highs, dtypes = [], [], set()
    for stem, _ in picked:
        info = inspect_chip(
            city_dir / "source" / f"{stem}.tif",
            tuple(config.get("rgb_bands", (1, 2, 3))),
        )
        bbox, dtype, band_highs = info["bbox"], info["dtype"], info["highs"]
        dtypes.add(dtype)
        highs.append(band_highs)
        entries.append(
            {
                "chip_id": stem,
                "tif": f"ramp_{city}/source/{stem}.tif",
                "geojson": f"ramp_{city}/labels/{stem}.geojson",
                "bbox": bbox,
                "split": splits[stem],
                "footprint": info["footprint"],
                "raster_metadata": info,
            }
        )
    rescale = "rescale=0,255"
    if dtypes != {"uint8"}:
        band_highs = np.median(np.array(highs), axis=0)
        rescale = "&".join(f"rescale=0,{h:.8g}" for h in band_highs)
    bands = "&".join(f"bidx={b}" for b in config.get("rgb_bands", (1, 2, 3)))
    west, south, east, north = (
        min(e["bbox"][0] for e in entries),
        min(e["bbox"][1] for e in entries),
        max(e["bbox"][2] for e in entries),
        max(e["bbox"][3] for e in entries),
    )
    return {
        "key": f"ramp_{city}",
        "name": f"RAMP - {city_title(city)}",
        "dataset_key": "ramp",
        "default_zoom": DEFAULT_ZOOM,
        "tiler_params": f"{bands}&{rescale}",
        "scene_imagery": "per_chip",
        "geometry_policy": "polygon-v1",
        "footprint_semantics": "roof",
        "campaign_bbox": {"west": west, "south": south, "east": east, "north": north},
        "chips": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=pathlib.Path, default=CONFIG)
    parser.add_argument("--cities", nargs="+", help="Override the config's cities")
    parser.add_argument("--scenes-per-city", type=int, help="Override the config")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.cities:
        config["cities"] = args.cities
    if args.scenes_per_city:
        config["scenes_per_city"] = args.scenes_per_city
    available = sorted(
        p.name.removeprefix("ramp_") for p in RAMP_DIR.glob("ramp_*") if p.is_dir()
    )
    unknown = set(config["cities"]) - set(available)
    if unknown:
        print(
            f"error: unknown cities {sorted(unknown)}; available: {available}",
            file=sys.stderr,
        )
        return 2

    campaigns = []
    for city in config["cities"]:
        campaign = build_city(city, config, random.Random(f"{config['seed']}:{city}"))
        campaigns.append(campaign)
        counts = {}
        for chip in campaign["chips"]:
            counts[chip["split"]] = counts.get(chip["split"], 0) + 1
        print(
            f"{campaign['key']}: {len(campaign['chips'])} scenes {counts}", flush=True
        )
    out = RAMP_DIR / MANIFEST_NAME
    out.write_text(json.dumps({"campaigns": campaigns}, indent=1), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

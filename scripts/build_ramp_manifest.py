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
import pathlib
import random
import sys

import numpy as np
import rasterio
from rasterio.warp import transform_bounds

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


def chip_raster(tif: pathlib.Path) -> tuple[list[float], str, list[float]]:
    """WGS84 bbox, dtype and each RGB band's bright-end stretch value of one chip."""
    with rasterio.open(tif) as r:
        bbox = transform_bounds(r.crs, "EPSG:4326", *r.bounds)
        dtype = r.dtypes[0]
        highs = [255.0] * 3
        if dtype != "uint8":
            highs = [
                float(np.percentile(r.read(b), STRETCH_PERCENTILE)) for b in (1, 2, 3)
            ]
    return [round(v, 8) for v in bbox], dtype, highs


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
        bbox, dtype, band_highs = chip_raster(city_dir / "source" / f"{stem}.tif")
        dtypes.add(dtype)
        highs.append(band_highs)
        entries.append(
            {
                "chip_id": stem,
                "tif": f"ramp_{city}/source/{stem}.tif",
                "geojson": f"ramp_{city}/labels/{stem}.geojson",
                "bbox": bbox,
                "split": splits[stem],
            }
        )
    rescale = "rescale=0,255"
    if dtypes != {"uint8"}:
        band_highs = np.median(np.array(highs), axis=0)
        rescale = "&".join(f"rescale=0,{int(h)}" for h in band_highs)
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
        "tiler_params": f"bidx=1&bidx=2&bidx=3&{rescale}",
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

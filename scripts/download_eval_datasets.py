"""Download the building-footprint evaluation datasets into data/datasets/.

Resumable: a file whose size already matches the listing is skipped, and partial
downloads land in a .part file first. Each dataset writes a _manifest.csv of what
it fetched so a later run can be checked against the source listing.

    python scripts/download_eval_datasets.py ramp spacenet2 belo_horizonte linz
    LINZ_API_KEY=... python scripts/download_eval_datasets.py linz_outlines  (WFS-scoped key)
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "data" / "datasets"
UA = {"User-Agent": "Mozilla/5.0 (stacnotator dataset downloader)"}

SPACENET = "https://spacenet-dataset.s3.amazonaws.com/"
SN2_SKIPPED_PRODUCTS = ("/MS/", "/PAN/", "/PS-MS/", "/tarballs/")
LINZ_IMAGERY = "https://nz-imagery.s3-ap-southeast-2.amazonaws.com/"
LINZ_SURVEYS = (
    "wellington/hutt-city_2021_0.075m/",
    "southland/invercargill_2022_0.05m/",
    "gisborne/gisborne_2022-2023_0.1m/",
)
LINZ_OUTLINES_LAYER = 101292
WFS_PAGE = 5000
BH_CKAN = "https://dados.pbh.gov.br/api/3/action/package_show?id=edificacao"
BH_ORTHO_2015_DRIVE_FOLDER = "1spGkQ1mcGtRRE5MNKsOrzIk5DdjJUKyI"
DRIVE_QUOTA_WAIT_S = 30 * 60

SOURCES = {
    "ramp": "RAMP building footprints (source.coop/ramp/ramp). Subset READMEs: CC-BY-SA-4.0; "
    "collection page: CC BY-NC 4.0 - resolve before redistribution.",
    "spacenet2": "SpaceNet 2 buildings, training split, all four AOIs (CC BY-SA 4.0): PS-RGB "
    "imagery, geojson_buildings labels and solutions CSVs. The MS, PAN and PS-MS bands and the "
    "unlabelled test_public split are not downloaded.",
    "belo_horizonte": "Belo Horizonte edificacao (dados.pbh.gov.br, cc-by, EPSG:31983) and the "
    "2015 orthophoto, 346 sheets at 1:2000 (prefeitura.pbh.gov.br/bhgeo/acervo-cartografico).",
    "linz": "LINZ aerial imagery (nz-imagery S3, CC BY 4.0, EPSG:2193) for "
    + ", ".join(LINZ_SURVEYS)
    + ". Outlines: NZ Building Outlines (All Sources, layer 101292, CC BY 4.0) via LDS WFS "
    "over each survey's bbox, all lifecycles and capture sources; the labels for a survey "
    "are the 'Current' features whose capture_source_name is that survey.",
}


def get(url: str, headers: dict | None = None, retries: int = 5) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def s3_list(base: str, prefix: str):
    token = None
    while True:
        url = f"{base}?list-type=2&prefix={urllib.parse.quote(prefix)}"
        if token:
            url += f"&continuation-token={urllib.parse.quote(token)}"
        xml = get(url).decode()
        for key, size in re.findall(
            r"<Key>([^<]+)</Key>.*?<Size>(\d+)</Size>", xml, re.S
        ):
            yield key, int(size)
        match = re.search(r"<NextContinuationToken>([^<]+)", xml)
        if not match:
            return
        token = match.group(1)


def fetch(url: str, dest: Path, size: int | None, retries: int = 5) -> int:
    if dest.exists() and (size is None or dest.stat().st_size == size):
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=300
            ) as r:
                with open(part, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
            if size is not None and part.stat().st_size != size:
                raise OSError(f"size {part.stat().st_size} != {size}")
            part.replace(dest)
            return dest.stat().st_size
        except Exception as e:
            if attempt == retries - 1:
                print(f"FAILED {url}: {e}", file=sys.stderr, flush=True)
                return 0
            time.sleep(2**attempt)
    return 0


def mirror(
    name: str, files: list[tuple[str, Path, int | None]], workers: int = 16
) -> None:
    out = ROOT / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "SOURCE.txt").write_text(SOURCES[name.split("/")[0]] + "\n")
    total = sum(s or 0 for _, _, s in files)
    print(f"[{name}] {len(files)} files, {total / 1e9:.2f} GB listed", flush=True)
    with ThreadPoolExecutor(workers) as pool:
        for i, _ in enumerate(pool.map(lambda f: fetch(*f), files), 1):
            if i % 500 == 0 or i == len(files):
                print(f"[{name}] {i}/{len(files)}", flush=True)
    with open(out / "_manifest.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "url", "size", "present"])
        for url, dest, size in files:
            ok = dest.exists() and (size is None or dest.stat().st_size == size)
            writer.writerow([dest.relative_to(out).as_posix(), url, size, ok])
    missing = sum(
        1
        for _, d, s in files
        if not (d.exists() and (s is None or d.stat().st_size == s))
    )
    print(f"[{name}] finished, {missing} missing", flush=True)


def ramp() -> None:
    base = "https://data.source.coop/ramp/ramp"
    files = [
        (
            f"https://data.source.coop/ramp/{key}",
            ROOT / "ramp" / key.removeprefix("ramp/"),
            size,
        )
        for key, size in s3_list(base, "")
        if not key.startswith("ramp/.source/")
    ]
    mirror("ramp", files, workers=32)


def spacenet2() -> None:
    files = [
        (
            SPACENET + key,
            ROOT / "spacenet2" / key.removeprefix("spacenet/SN2_buildings/"),
            size,
        )
        for key, size in s3_list(SPACENET, "spacenet/SN2_buildings/train/")
        if not any(p in key for p in SN2_SKIPPED_PRODUCTS)
    ]
    mirror("spacenet2", files)


def linz() -> None:
    files = [
        (LINZ_IMAGERY + key, ROOT / "linz" / "imagery" / key, size)
        for survey in LINZ_SURVEYS
        for key, size in s3_list(LINZ_IMAGERY, survey)
    ]
    mirror("linz", files)


def linz_outlines() -> None:
    key = os.environ.get("LINZ_API_KEY")
    if not key:
        sys.exit("linz_outlines needs LINZ_API_KEY (free LINZ Data Service account)")
    out = ROOT / "linz" / "outlines"
    out.mkdir(parents=True, exist_ok=True)
    for survey in LINZ_SURVEYS:
        dest = out / f"{survey.split('/')[1]}.geojson"
        if dest.exists():
            continue
        west, south, east, north = json.loads(
            get(LINZ_IMAGERY + survey + "rgb/2193/collection.json")
        )["extent"]["spatial"]["bbox"][0]
        features: list[dict] = []
        while True:
            query = urllib.parse.urlencode(
                {
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeNames": f"layer-{LINZ_OUTLINES_LAYER}",
                    "outputFormat": "application/json",
                    "bbox": f"{south},{west},{north},{east},urn:ogc:def:crs:EPSG::4326",
                    "sortBy": "building_outline_id",
                    "count": WFS_PAGE,
                    "startIndex": len(features),
                }
            )
            page = json.loads(
                get(f"https://data.linz.govt.nz/services;key={key}/wfs?{query}")
            )
            features += page["features"]
            print(f"[linz_outlines] {dest.name}: {len(features)}", flush=True)
            if len(page["features"]) < WFS_PAGE:
                break
        collection = {
            "type": "FeatureCollection",
            "crs": page["crs"],
            "features": features,
        }
        dest.write_text(json.dumps(collection))


def belo_horizonte() -> None:
    package = json.loads(get(BH_CKAN))["result"]

    def filename(resource: dict) -> str:
        return Path(urllib.parse.urlparse(resource["url"]).path).name

    resources = sorted(package["resources"], key=filename, reverse=True)
    csvs = [r for r in resources if r.get("format", "").lower() == "csv"]
    docs = [r for r in resources if r.get("format", "").lower() != "csv"]
    files = [
        (r["url"], ROOT / "belo_horizonte" / "edificacao" / filename(r), None)
        for r in csvs[:1] + docs
    ]
    mirror("belo_horizonte", files, workers=4)

    import gdown

    ortho = ROOT / "belo_horizonte" / "ortofoto_2015"
    ortho.mkdir(parents=True, exist_ok=True)
    sheets = gdown.download_folder(
        id=BH_ORTHO_2015_DRIVE_FOLDER, skip_download=True, quiet=True
    )
    pending = [s for s in sheets if not (ortho / Path(s.path).name).exists()]
    while pending:
        sheet = pending[0]
        dest = ortho / Path(sheet.path).name
        part = dest.with_name(dest.name + ".part")
        try:
            gdown.download(id=sheet.id, output=str(part), quiet=True)
            part.replace(dest)
            pending.pop(0)
            print(
                f"[belo_horizonte] {len(sheets) - len(pending)}/{len(sheets)} sheets",
                flush=True,
            )
        except Exception as e:
            print(
                f"[belo_horizonte] drive refused {dest.name} ({type(e).__name__}),"
                f" {len(pending)} left; retrying in {DRIVE_QUOTA_WAIT_S // 60} min",
                flush=True,
            )
            time.sleep(DRIVE_QUOTA_WAIT_S)
    print(f"[belo_horizonte] ortofoto_2015: {len(sheets)} files complete", flush=True)


DATASETS = {
    "ramp": ramp,
    "spacenet2": spacenet2,
    "belo_horizonte": belo_horizonte,
    "linz": linz,
    "linz_outlines": linz_outlines,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("datasets", nargs="+", choices=DATASETS)
    for dataset in parser.parse_args().datasets:
        DATASETS[dataset]()

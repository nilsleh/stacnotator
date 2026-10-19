"""Seed the local building campaigns from a curated manifest.

Checks the curated files, copies the chips into the tiler's COG directory
(cogs/curated, mounted at /data/cogs/curated), then runs the backend's seeder inside the
backend container. Chips are copied under a content-hash name, so neither the public
dataset's file names nor its folder layout reach the tiler or the app. Existing
campaigns are reused unchanged; --reset recreates them and deletes their annotations, so
it needs --confirm-reset DELETE-ANNOTATIONS.

    python scripts/seed_building_datasets.py                  # all datasets
    python scripts/seed_building_datasets.py --only sn1_rio   # one dataset
    python scripts/seed_building_datasets.py --manifest data/datasets/ramp/stacnotator_manifest.json
    python scripts/seed_building_datasets.py --verify-only    # check files, change nothing
    python scripts/seed_building_datasets.py --refresh-imagery  # re-register chips on the tiler
"""

import argparse
import copy
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "imagery" / "curated_subsets_manifest.json"
COG_DIR = ROOT / "cogs" / "curated"
CONTAINER = "backend-dev"
CONTAINER_PAYLOAD = "/tmp/building_seed.json"


def tifs_of(dataset: dict) -> set[str]:
    tifs = {c["tif"] for c in dataset["chips"]}
    tifs |= {c["secondary_tif"] for c in dataset["chips"] if c.get("secondary_tif")}
    tifs |= {s["tif"] for s in dataset.get("slices", [])}
    return tifs


def check_files(datasets: list[dict], data_dir: pathlib.Path) -> list[str]:
    missing = []
    for d in datasets:
        for rel in sorted(tifs_of(d) | {c["geojson"] for c in d["chips"]}):
            if not (data_dir / rel).is_file():
                missing.append(rel)
    return missing


def cog_names(datasets: list[dict], data_dir: pathlib.Path) -> dict[str, str]:
    names = {}
    for rel in sorted(set().union(*(tifs_of(d) for d in datasets))):
        digest = hashlib.sha256((data_dir / rel).read_bytes()).hexdigest()[:24]
        names[rel] = f"{digest}.tif"
    return names


def sync_cogs(
    names: dict[str, str], data_dir: pathlib.Path, cog_dir: pathlib.Path
) -> int:
    copied = 0
    cog_dir.mkdir(parents=True, exist_ok=True)
    for rel, name in names.items():
        src, dst = data_dir / rel, cog_dir / name
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            continue
        shutil.copy2(src, dst)
        copied += 1
    return copied


def with_cog_names(datasets: list[dict], names: dict[str, str]) -> list[dict]:
    renamed = copy.deepcopy(datasets)
    for d in renamed:
        for chip in d["chips"]:
            chip["tif"] = names[chip["tif"]]
            if chip.get("secondary_tif"):
                chip["secondary_tif"] = names[chip["secondary_tif"]]
        for s in d.get("slices", []):
            s["tif"] = names[s["tif"]]
    return renamed


def build_payload(
    datasets: list[dict], data_dir: pathlib.Path, names: dict[str, str]
) -> dict:
    geojsons = {
        c["geojson"]: json.loads((data_dir / c["geojson"]).read_text(encoding="utf-8"))
        for d in datasets
        for c in d["chips"]
    }
    return {"datasets": with_cog_names(datasets, names), "geojsons": geojsons}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=MANIFEST,
        help="Curated manifest; chip paths are relative to its folder",
    )
    parser.add_argument("--only", nargs="+", help="Dataset keys (default: all)")
    parser.add_argument("--project", default="Buildings (local)", help="Project name")
    parser.add_argument(
        "--user", default="local-user", help="Owner/assignee external uid"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Recreate existing campaigns"
    )
    parser.add_argument(
        "--confirm-reset", help="Required with --reset: DELETE-ANNOTATIONS"
    )
    parser.add_argument("--verify-only", action="store_true", help="Check files only")
    parser.add_argument(
        "--refresh-imagery",
        action="store_true",
        help="Re-register existing campaigns' chips on the tiler (after restoring a backup)",
    )
    parser.add_argument("--container", default=CONTAINER, help="Backend container name")
    args = parser.parse_args()

    data_dir = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    datasets = manifest["campaigns"]
    if args.only:
        unknown = set(args.only) - {d["key"] for d in datasets}
        if unknown:
            print(f"error: unknown dataset(s) {sorted(unknown)}", file=sys.stderr)
            return 2
        datasets = [d for d in datasets if d["key"] in args.only]

    missing = check_files(datasets, data_dir)
    if missing:
        print(
            "error: missing curated files:\n  " + "\n  ".join(missing), file=sys.stderr
        )
        return 1
    print(f"{len(datasets)} dataset(s), all files present", flush=True)
    if args.verify_only:
        return 0

    names = cog_names(datasets, data_dir)
    copied = sync_cogs(names, data_dir, COG_DIR)
    print(f"cogs/curated: {copied} file(s) copied", flush=True)

    fd, tmp = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(build_payload(datasets, data_dir, names), fh)
        run(["docker", "cp", tmp, f"{args.container}:{CONTAINER_PAYLOAD}"])
        cmd = [
            "docker",
            "exec",
            "-w",
            "/app",
            args.container,
            "python",
            "-m",
            "src.buildings.seeding",
            CONTAINER_PAYLOAD,
            "--project",
            args.project,
            "--user",
            args.user,
        ]
        if args.only:
            cmd += ["--only", *args.only]
        if args.reset:
            cmd.append("--reset")
        if args.confirm_reset:
            cmd += ["--confirm-reset", args.confirm_reset]
        if args.refresh_imagery:
            cmd.append("--refresh-imagery")
        result = subprocess.run(cmd)
        subprocess.run(
            ["docker", "exec", args.container, "rm", "-f", CONTAINER_PAYLOAD]
        )
        return result.returncode
    finally:
        os.unlink(tmp)


if __name__ == "__main__":
    sys.exit(main())

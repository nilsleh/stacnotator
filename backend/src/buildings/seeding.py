"""Seed local building campaigns from the curated dataset manifest.

One campaign per curated dataset, one scene (task) per chip, the chips themselves as the
imagery, and the dataset's reference footprints imported as standalone annotations.

The chips are registered on the hosted tiler from its local COG directory
(``TILER_LOCAL_COG_DIR``) and served as imagery-source slices whose tile URL carries the
dataset's bands and stretch. Custom maps can't do this: they render one band through a
colormap.

Run inside the backend container; ``scripts/seed_building_datasets.py`` on the host builds
the payload (manifest plus reference GeoJSON) and calls::

    python -m src.buildings.seeding /tmp/building_seed.json --project "Buildings (local)"
"""

import argparse
import hashlib
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import parse_qsl
from uuid import UUID

import shapely
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon, box, shape
from shapely.ops import transform
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from src.annotation.ingest import insert_tasks
from src.annotation.models import (
    Annotation,
    AnnotationGeometry,
    AnnotationTask,
    AnnotationTaskAssignment,
)
from src.auth.models import User
from src.buildings.config import BuildingScenes
from src.campaigns.models import Campaign, TaskSet
from src.campaigns.schemas import CampaignSettingsCreate, LabelBase
from src.campaigns.service import create_campaign, delete_campaign
from src.campaigns.task_sets import DEFAULT_TASK_SET_NAME
from src.config import TilerCfg, get_settings
from src.imagery.models import ImageryCollection, ImagerySlice, ImagerySource, SliceTileUrl
from src.imagery.schemas import (
    BasemapCreate,
    ImageryCollectionCreate,
    ImageryEditorStateCreate,
    ImagerySliceCreate,
    ImagerySourceCreate,
    ImageryViewCreate,
    SliceTileUrlCreate,
    VisualizationTemplateCreate,
)
from src.imagery.service import create_imagery_from_editor_state, create_view
from src.projects.models import Project
from src.tilers.providers import (
    build_tile_url,
    register_cog_on_tiler,
    register_on_tiler,
    resolve_tiler,
)

logger = logging.getLogger(__name__)

RESET_CONFIRMATION = "DELETE-ANNOTATIONS"
BUILDING_SCENES = BuildingScenes(building_label_id=1, complete_label_id=2, empty_label_id=3)
LABELS = [
    LabelBase(id=1, name="Building", geometry_type="polygon"),
    LabelBase(id=2, name="Buildings mapped"),
    LabelBase(id=3, name="No buildings"),
]
VISUALIZATION = "RGB"
COG_SUBDIR = "curated"
# Registering a COG builds its STAC item on the tiler, which takes 10-50 s locally.
REGISTER_TIMEOUT_S = 600
REGISTER_WORKERS = 4
# How the tiler stores a registered COG (stacnotator-tiler ``tiler/register_cog.py``):
# one item in the shared collection, with an id derived from the COG URL. A mosaic
# search over several chips names those item ids.
TILER_COG_COLLECTION = "custom-maps"
TILER_COG_ASSET = "data"
BASEMAP = BasemapCreate(
    name="CartoDB Positron",
    url="https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    max_native_zoom=19,
)
WGS84 = CRS.from_epsg(4326)


@dataclass(frozen=True)
class SliceSpec:
    name: str
    start_date: str
    end_date: str
    tifs: tuple[str, ...]


@dataclass(frozen=True)
class SourceSpec:
    name: str
    default_zoom: int
    viz_params: dict
    slices: tuple[SliceSpec, ...]


@dataclass
class SceneSpec:
    chip_id: str
    cell: Polygon
    raw_source_data: dict
    references: list[Polygon | MultiPolygon] = field(default_factory=list)


@dataclass(frozen=True)
class SeedResult:
    key: str
    campaign_id: int
    created: bool
    scenes: int = 0
    references: int = 0


def tiler_cog_item_id(cog_url: str) -> str:
    """The id the tiler gives the item it creates for ``cog_url``."""
    return "cm-" + hashlib.sha1(cog_url.encode(), usedforsecurity=False).hexdigest()[:16]


def parse_tiler_params(params: str) -> dict:
    """``bidx=5&bidx=3&bidx=2&rescale=0,1200`` -> viz params for ``build_tile_url``."""
    bidx: list[int] = []
    rescale: str | None = None
    for key, value in parse_qsl(params, strict_parsing=True):
        if key == "bidx":
            bidx.append(int(value))
        elif key == "rescale":
            lo, hi = (float(v) for v in value.split(","))
            if hi <= lo:
                raise ValueError(f"rescale must be low,high: {value!r}")
            rescale = value
        else:
            raise ValueError(f"unsupported tiler param {key!r}")
    viz: dict = {"assets": [TILER_COG_ASSET]}
    if bidx:
        viz["bidx"] = bidx
    if rescale:
        viz["rescale"] = rescale
    return viz


def plan_sources(dataset: dict) -> list[SourceSpec]:
    """The imagery a dataset is shown with.

    A dataset with ``slices`` (off-nadir angles, monthly mosaics) gets one time slice per
    image; otherwise every chip is mosaicked into a single slice. A ``secondary_source``
    (e.g. Khartoum's multispectral chips) becomes a second source over the same chips.
    """
    zoom = int(dataset["default_zoom"])
    if dataset.get("slices"):
        slices = tuple(
            SliceSpec(s["name"], s["start_date"], s["end_date"], (s["tif"],))
            for s in dataset["slices"]
        )
    else:
        slices = (
            SliceSpec(
                "All chips", "2020-01-01", "2020-12-31", tuple(c["tif"] for c in dataset["chips"])
            ),
        )
    sources = [
        SourceSpec(
            f"{dataset['dataset_key'].upper()} Primary",
            zoom,
            parse_tiler_params(dataset["tiler_params"]),
            slices,
        )
    ]
    secondary = dataset.get("secondary_source")
    if secondary:
        tifs = tuple(c["secondary_tif"] for c in dataset["chips"] if c.get("secondary_tif"))
        if not tifs:
            raise ValueError(f"{dataset['key']}: secondary_source but no chip has secondary_tif")
        sources.append(
            SourceSpec(
                secondary["name"],
                zoom,
                parse_tiler_params(secondary["tiler_params"]),
                (SliceSpec("All chips", "2020-01-01", "2020-12-31", tifs),),
            )
        )
    return sources


def _source_crs(feature_collection: dict) -> CRS:
    name = ((feature_collection.get("crs") or {}).get("properties") or {}).get("name")
    return CRS.from_user_input(name) if name else WGS84


def normalize_references(feature_collection: dict, cell: Polygon) -> list[Polygon | MultiPolygon]:
    """Reference footprints in WGS84, 2D, valid and clipped to the scene's cell.

    The curated GeoJSON comes in its source CRS (CRS84, UTM, Web Mercator) and some of it
    carries a zero Z. Footprints outside the cell are dropped; ones crossing it are clipped.
    """
    crs = _source_crs(feature_collection)
    to_wgs84 = (
        None if crs.equals(WGS84) else Transformer.from_crs(crs, WGS84, always_xy=True).transform
    )
    out: list[Polygon | MultiPolygon] = []
    for feature in feature_collection.get("features") or []:
        if not feature.get("geometry"):
            continue
        geom = shapely.force_2d(shape(feature["geometry"]))
        if to_wgs84 is not None:
            geom = transform(to_wgs84, geom)
        geom = shapely.make_valid(geom)
        if not geom.intersects(cell):
            continue
        clipped = _polygonal(geom.intersection(cell))
        if clipped is not None:
            out.append(clipped)
    return out


def _polygonal(geom) -> Polygon | MultiPolygon | None:
    if isinstance(geom, Polygon):
        parts = [geom]
    elif isinstance(geom, MultiPolygon):
        parts = list(geom.geoms)
    elif hasattr(geom, "geoms"):
        parts = [g for g in geom.geoms if isinstance(g, Polygon)]
    else:
        parts = []
    parts = [p for p in parts if not p.is_empty and p.area > 0]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def plan_scenes(dataset: dict, geojsons: dict[str, dict], cog_dir: str) -> list[SceneSpec]:
    """One scene per chip: the chip's bbox is the cell, its footprints the references."""
    scenes = []
    for chip in dataset["chips"]:
        cell = box(*chip["bbox"])
        references = normalize_references(geojsons[chip["geojson"]], cell)
        raw = {
            "chip_id": chip["chip_id"],
            "dataset_key": dataset["dataset_key"],
            "subset": dataset["key"],
            "split": chip.get("split"),
            "cog_href": f"{cog_dir}/{chip['tif']}",
            "reference_buildings": len(references),
        }
        if chip.get("slice_id"):
            raw["slice_id"] = chip["slice_id"]
        if not references:
            raw["is_empty_reference"] = True
        scenes.append(SceneSpec(chip["chip_id"], cell, raw, references))
    return scenes


class TilerRegistrar:
    """Registers chips on the hosted tiler under one campaign's tile scope."""

    def __init__(self, tiler_name: str, tiler: TilerCfg, scope: str, cog_dir: str):
        self.tiler_name = tiler_name
        self.tiler = tiler
        self.scope = scope
        self.cog_dir = cog_dir
        # tile URL -> search id, for stamping the saved slice tile URLs
        self.searches: dict[str, str] = {}

    def _register_cog(self, tif: str) -> str:
        return register_cog_on_tiler(
            self.tiler, f"{self.cog_dir}/{tif}", self.scope, timeout=REGISTER_TIMEOUT_S
        )

    def search(self, tifs: tuple[str, ...]) -> str:
        """A search over these chips: the COG's own search for one, a mosaic for several."""
        with ThreadPoolExecutor(max_workers=REGISTER_WORKERS) as pool:
            search_ids = list(pool.map(self._register_cog, tifs))
        if len(tifs) == 1:
            return search_ids[0]
        item_ids = [tiler_cog_item_id(f"{self.cog_dir}/{tif}") for tif in tifs]
        return register_on_tiler(
            self.tiler, {"collections": [TILER_COG_COLLECTION], "ids": item_ids}, self.scope
        )

    def tile_url(self, search_id: str, viz_params: dict) -> str:
        url = build_tile_url("hosted", search_id, viz_params, tiler=self.tiler)
        self.searches[url] = search_id
        return url

    def stamp(self, db: Session, campaign_id: int) -> None:
        """Mark the campaign's tile URLs as served by our tiler, as registration does.

        The browser only sends the tiler cookie for tile URLs with a self-hosted
        ``tile_provider``; without it every chip tile is refused.
        """
        rows = db.scalars(
            select(SliceTileUrl)
            .join(ImagerySlice, SliceTileUrl.slice_id == ImagerySlice.id)
            .join(ImageryCollection, ImagerySlice.collection_id == ImageryCollection.id)
            .join(ImagerySource, ImageryCollection.source_id == ImagerySource.id)
            .where(ImagerySource.campaign_id == campaign_id)
        ).all()
        for row in rows:
            row.tile_provider = self.tiler_name
            row.mosaic_id = self.searches[row.tile_url]


def build_editor_state(
    sources: list[SourceSpec], registrar: TilerRegistrar
) -> ImageryEditorStateCreate:
    return ImageryEditorStateCreate(
        sources=[
            ImagerySourceCreate(
                name=src.name,
                default_zoom=src.default_zoom,
                visualizations=[VisualizationTemplateCreate(name=VISUALIZATION)],
                collections=[
                    ImageryCollectionCreate(
                        name="Chips",
                        slices=[
                            ImagerySliceCreate(
                                name=sl.name,
                                start_date=sl.start_date,
                                end_date=sl.end_date,
                                tile_urls=[
                                    SliceTileUrlCreate(
                                        visualization_name=VISUALIZATION,
                                        tile_url=registrar.tile_url(
                                            registrar.search(sl.tifs), src.viz_params
                                        ),
                                    )
                                ],
                            )
                            for sl in src.slices
                        ],
                    )
                ],
            )
            for src in sources
        ],
        basemaps=[BASEMAP],
    )


def _campaigns_named(db: Session, project_id: int, name: str) -> list[Campaign]:
    return list(
        db.scalars(
            select(Campaign)
            .where(Campaign.project_id == project_id, Campaign.name == name)
            .order_by(Campaign.id)
        )
    )


def _insert_references(
    db: Session, campaign_id: int, user_id: UUID, scenes: list[SceneSpec]
) -> int:
    """Reference footprints as standalone annotations, each with its own ``source_id``.

    They carry no task and no ``origin_task_id``, so they never join a scene's buildings
    or count toward completion; ``source_id`` (unique per campaign) marks them as imported.
    """
    geoms = [g for scene in scenes for g in scene.references]
    if not geoms:
        return 0
    geometry_ids = db.scalars(
        insert(AnnotationGeometry).returning(AnnotationGeometry.id),
        [{"geometry": f"SRID=4326;{g.wkt}"} for g in geoms],
    ).all()
    db.execute(
        insert(Annotation),
        [
            {
                "geometry_id": gid,
                "campaign_id": campaign_id,
                "created_by_user_id": user_id,
                "label_id": BUILDING_SCENES.building_label_id,
                "source_id": source_id,
            }
            for source_id, gid in enumerate(geometry_ids, start=1)
        ],
    )
    return len(geoms)


def _create_campaign(
    db: Session,
    dataset: dict,
    scenes: list[SceneSpec],
    project_id: int,
    user_id: UUID,
    tiler_name: str,
    tiler: TilerCfg,
    cog_dir: str,
) -> Campaign:
    bbox = dataset["campaign_bbox"]
    campaign = create_campaign(
        db,
        name=dataset["name"],
        mode="tasks",
        project_id=project_id,
        settings=CampaignSettingsCreate(
            labels=LABELS,
            bbox_west=bbox["west"],
            bbox_south=bbox["south"],
            bbox_east=bbox["east"],
            bbox_north=bbox["north"],
        ),
        user_id=user_id,
    )
    campaign_id = campaign.id
    BUILDING_SCENES.check_labels(campaign.settings.labels)
    campaign.settings.building_scenes = BUILDING_SCENES.model_dump()
    # The tiler calls take minutes; don't sit idle in a transaction meanwhile.
    db.commit()
    try:
        # Searches are scoped to the campaign, so its id must exist before the imagery does.
        registrar = TilerRegistrar(tiler_name, tiler, str(campaign_id), cog_dir)
        state = build_editor_state(plan_sources(dataset), registrar)
        create_imagery_from_editor_state(db, campaign=campaign, editor_state=state)
        db.flush()
        registrar.stamp(db, campaign_id)
        db.commit()
        db.refresh(campaign)
        create_view(
            db,
            campaign,
            ImageryViewCreate(
                name="Default View", source_ids=[s.id for s in campaign.imagery_sources]
            ),
        )
        task_set_id = db.scalar(
            select(TaskSet.id).where(
                TaskSet.campaign_id == campaign_id, TaskSet.name == DEFAULT_TASK_SET_NAME
            )
        )
        insert_tasks(
            db,
            campaign_id,
            task_set_id,
            [s.cell.wkt for s in scenes],
            [s.raw_source_data for s in scenes],
        )
        task_ids = db.scalars(
            select(AnnotationTask.id)
            .where(AnnotationTask.campaign_id == campaign_id)
            .order_by(AnnotationTask.annotation_number)
        ).all()
        db.execute(
            insert(AnnotationTaskAssignment),
            [{"task_id": tid, "user_id": user_id} for tid in task_ids],
        )
        _insert_references(db, campaign_id, user_id, scenes)
        db.commit()
    except BaseException:
        db.rollback()
        delete_campaign(db, campaign_id)
        raise
    return campaign


def seed(
    db: Session,
    payload: dict,
    *,
    project_name: str,
    user_uid: str,
    reset: bool = False,
    confirm_reset: str | None = None,
    only: set[str] | None = None,
    tiler_name: str | None = None,
) -> list[SeedResult]:
    """Create each dataset's campaign, reusing one that already exists by name.

    ``reset`` deletes the existing campaigns first, with every annotation in them, so it
    needs ``confirm_reset=RESET_CONFIRMATION``.
    """
    if reset and confirm_reset != RESET_CONFIRMATION:
        raise ValueError(
            f"--reset deletes every annotation in these campaigns; back up first, then pass "
            f"--confirm-reset {RESET_CONFIRMATION}"
        )
    settings = get_settings()
    local_dir = settings.TILER_LOCAL_COG_DIR
    if not local_dir:
        raise ValueError("TILER_LOCAL_COG_DIR must be set so the tiler can read the chips")
    cog_dir = f"{local_dir}/{COG_SUBDIR}"
    tiler_name = tiler_name or settings.DEFAULT_TILER
    if not tiler_name:
        raise ValueError("No tiler: set DEFAULT_TILER")
    tiler = resolve_tiler(tiler_name)

    project = db.scalar(select(Project).where(Project.name == project_name))
    if project is None:
        raise ValueError(f"No project named {project_name!r}")
    user = db.scalar(select(User).where(User.external_uid == user_uid))
    if user is None:
        raise ValueError(f"No user with external uid {user_uid!r}")

    datasets = [d for d in payload["datasets"] if only is None or d["key"] in only]
    if only is not None and len(datasets) != len(only):
        raise ValueError(f"Unknown dataset(s): {sorted(only - {d['key'] for d in datasets})}")
    # Validate every dataset before touching the database or the tiler.
    planned = [(d, plan_scenes(d, payload["geojsons"], cog_dir), plan_sources(d)) for d in datasets]

    results = []
    for dataset, scenes, _ in planned:
        existing = _campaigns_named(db, project.id, dataset["name"])
        if existing and not reset:
            results.append(SeedResult(dataset["key"], existing[0].id, created=False))
            continue
        for old in existing:
            delete_campaign(db, old.id)
        project_id, user_id = project.id, user.id
        db.commit()
        campaign = _create_campaign(
            db, dataset, scenes, project_id, user_id, tiler_name, tiler, cog_dir
        )
        results.append(
            SeedResult(
                dataset["key"],
                campaign.id,
                created=True,
                scenes=len(scenes),
                references=sum(len(s.references) for s in scenes),
            )
        )
    return results


def main(argv: list[str] | None = None) -> int:
    import src.models  # noqa: F401  (registers every mapper)
    from src.database import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("payload", help="JSON built by scripts/seed_building_datasets.py")
    parser.add_argument("--project", default="Buildings (local)", help="Project name")
    parser.add_argument("--user", default="local-user", help="Owner/assignee external uid")
    parser.add_argument("--only", nargs="+", help="Dataset keys to seed (default: all)")
    parser.add_argument("--reset", action="store_true", help="Recreate existing campaigns")
    parser.add_argument("--confirm-reset", help=f"Required with --reset: {RESET_CONFIRMATION}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    with open(args.payload, encoding="utf-8") as fh:
        payload = json.load(fh)
    db = SessionLocal()
    try:
        results = seed(
            db,
            payload,
            project_name=args.project,
            user_uid=args.user,
            reset=args.reset,
            confirm_reset=args.confirm_reset,
            only=set(args.only) if args.only else None,
        )
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    finally:
        db.close()
    for r in results:
        if r.created:
            sys.stdout.write(
                f"created campaign {r.campaign_id} ({r.key}): {r.scenes} scenes, "
                f"{r.references} reference buildings\n"
            )
        else:
            sys.stdout.write(f"reused campaign {r.campaign_id} ({r.key}): unchanged\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

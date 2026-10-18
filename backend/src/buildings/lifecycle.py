"""Scene and building rules for building campaigns.

A scene is a task. Its buildings are the user's standalone polygon annotations whose
``origin_task_id`` is that task; its outcome is the user's task annotation (Complete and
Empty are labels, Skip is no label, Draft is no annotation while buildings exist). The
annotation service calls these hooks; on any other campaign they do nothing.

Every hook runs inside the caller's transaction and never commits.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import shapely
from fastapi import HTTPException
from geoalchemy2.shape import to_shape
from shapely import wkt as shapely_wkt
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from src.annotation.geometries import delete_rows_and_orphan_geometries
from src.annotation.models import Annotation, AnnotationTask, AnnotationTaskAssignment
from src.buildings.config import MAX_CONFIDENCE, MIN_CONFIDENCE, BuildingScenes, building_scenes
from src.buildings.models import BuildingHandIn
from src.campaigns.models import Campaign

SceneState = Literal["complete", "empty", "skipped", "draft", "todo"]
FINISHED: frozenset[str] = frozenset({"complete", "empty"})
# Drawn vertices that land on the scene outline may be a hair outside it after a
# round trip through the map projection; ~1 mm at the equator.
BOUNDARY_TOLERANCE_DEG = 1e-8


@dataclass(frozen=True)
class SceneSummary:
    task_id: int
    annotation_number: int
    state: SceneState
    buildings: int
    unscored: int


# ---------------------------------------------------------------------------
# Building geometry and fields
# ---------------------------------------------------------------------------


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def validate_building_geometry(geometry_wkt: str, cell: BaseGeometry) -> Polygon:
    """A building is one valid, finite, 2D polygon with area, inside its scene."""
    try:
        geom = shapely_wkt.loads(geometry_wkt)
    except Exception as exc:
        raise _bad("Building geometry is not valid WKT") from exc
    if not isinstance(geom, Polygon):
        raise _bad("A building must be a single polygon")
    if geom.is_empty or geom.area <= 0:
        raise _bad("A building polygon must have an area")
    if geom.has_z:
        raise _bad("A building polygon must be 2D")
    coords = shapely.get_coordinates(geom)
    if not all(math.isfinite(v) for v in coords.ravel()):
        raise _bad("A building polygon has non-finite coordinates")
    if not geom.is_valid:
        raise _bad(f"A building polygon is invalid: {explain_validity(geom)}")
    if not cell.buffer(BOUNDARY_TOLERANCE_DEG).covers(geom):
        raise _bad("A building must lie inside its scene; this one crosses the scene outline")
    return geom


def _check_confidence(confidence: int | None, *, required: bool) -> None:
    if confidence is None:
        if required:
            raise _bad(f"Every building needs a confidence of {MIN_CONFIDENCE}-{MAX_CONFIDENCE}")
        return
    if not MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE:
        raise _bad(f"Building confidence must be {MIN_CONFIDENCE}-{MAX_CONFIDENCE}")


def _scene_cells(db: Session, campaign_id: int, task_ids: Iterable[int]) -> dict[int, BaseGeometry]:
    ids = set(task_ids)
    if not ids:
        return {}
    tasks = db.scalars(
        select(AnnotationTask).where(
            AnnotationTask.campaign_id == campaign_id, AnnotationTask.id.in_(ids)
        )
    ).all()
    cells = {t.id: to_shape(t.geometry.geometry) for t in tasks}
    missing = ids - set(cells)
    if missing:
        raise _bad(f"Scene(s) not found in this campaign: {sorted(missing)}")
    return cells


def check_new_buildings(db: Session, campaign: Campaign, items: list) -> set[int]:
    """Validate standalone annotations about to be created; returns the scenes they join.

    ``items`` are ``AnnotationCreate``. In a building campaign every drawn shape is a
    building of a scene: the building label, a scene (``origin_task_id``), a confidence,
    and a geometry inside that scene. Nothing is written if any item fails.
    """
    cfg = building_scenes(campaign)
    if cfg is None:
        return set()
    for item in items:
        if item.label_id != cfg.building_label_id:
            raise _bad("Only buildings can be drawn in a building campaign")
        if item.origin_task_id is None:
            raise _bad("A building must belong to a scene (origin_task_id)")
        _check_confidence(item.confidence, required=True)
    cells = _scene_cells(db, campaign.id, {item.origin_task_id for item in items})
    for item in items:
        validate_building_geometry(item.geometry_wkt, cells[item.origin_task_id])
    return {item.origin_task_id for item in items}


def check_annotation_update(
    db: Session, campaign: Campaign, annotation: Annotation, update
) -> None:
    """Validate an edit (``AnnotationUpdate``) of a building or a scene outcome."""
    cfg = building_scenes(campaign)
    if cfg is None:
        return
    if annotation.annotation_task_id is not None:
        if update.label_id is not None and update.label_id != annotation.label_id:
            raise _bad("Change a scene's outcome with Complete, Empty, Skip or Draft")
        if update.is_authoritative:
            raise _bad("Building campaigns have no authoritative submissions")
        return
    if annotation.origin_task_id is None:
        return
    if update.label_id is not None and update.label_id != cfg.building_label_id:
        raise _bad("A building keeps the building label")
    _check_confidence(update.confidence, required=False)
    if annotation.confidence is None and update.confidence is None:
        # Legacy rows predate scoring; an edit is the moment to score them.
        raise _bad(f"Every building needs a confidence of {MIN_CONFIDENCE}-{MAX_CONFIDENCE}")
    if update.geometry_wkt is not None:
        cells = _scene_cells(db, campaign.id, [annotation.origin_task_id])
        validate_building_geometry(update.geometry_wkt, cells[annotation.origin_task_id])


def building_owners(annotations: Iterable[Annotation]) -> set[tuple[UUID, int]]:
    """(owner, scene) for every building among ``annotations``."""
    return {
        (a.created_by_user_id, a.origin_task_id)
        for a in annotations
        if a.origin_task_id is not None and a.annotation_task_id is None
    }


@dataclass(frozen=True)
class DeletionEffects:
    """Who a deletion touches, captured before the rows are gone."""

    building_owners: set[tuple[UUID, int]]
    outcome_owners: set[UUID]


def deletion_effects(campaign: Campaign, annotations: Iterable[Annotation]) -> DeletionEffects:
    if building_scenes(campaign) is None:
        return DeletionEffects(set(), set())
    annotations = list(annotations)
    return DeletionEffects(
        building_owners(annotations),
        {a.created_by_user_id for a in annotations if a.annotation_task_id is not None},
    )


def apply_deletion_effects(db: Session, campaign: Campaign, effects: DeletionEffects) -> list[int]:
    """Deleting a building reopens its scene; deleting an outcome unfinishes it. Either
    withdraws a hand-in that no longer holds. Returns reopened outcome ids to tombstone."""
    deleted = reopen_scenes(db, campaign, effects.building_owners)
    for user_id in effects.outcome_owners:
        revalidate_hand_in(db, campaign, user_id)
    return deleted


# ---------------------------------------------------------------------------
# Scene outcomes
# ---------------------------------------------------------------------------


def _building_counts(
    db: Session, user_id: UUID, task_ids: Iterable[int]
) -> dict[int, tuple[int, int]]:
    """task id -> (buildings, unscored) for one user's buildings in these scenes."""
    ids = list(task_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(
            Annotation.origin_task_id,
            func.count(),
            func.count().filter(Annotation.confidence.is_(None)),
        )
        .where(
            Annotation.origin_task_id.in_(ids),
            Annotation.created_by_user_id == user_id,
            Annotation.annotation_task_id.is_(None),
        )
        .group_by(Annotation.origin_task_id)
    ).all()
    return {task_id: (n, unscored) for task_id, n, unscored in rows}


def check_scene_outcome(
    db: Session, campaign: Campaign, task: AnnotationTask, user_id: UUID, submission
) -> None:
    """Validate a task submission (``AnnotationFromTaskCreate``) as a scene outcome.

    Complete needs at least one building and every building scored; Empty is refused
    while buildings remain (deleting them is the user's call, never a side effect);
    Skip (no label) keeps whatever was drawn.
    """
    cfg = building_scenes(campaign)
    if cfg is None:
        return
    if submission.is_authoritative:
        raise _bad("Building campaigns have no authoritative submissions")
    label_id = submission.label_id
    if label_id is None:
        return
    buildings, unscored = _building_counts(db, user_id, [task.id]).get(task.id, (0, 0))
    if label_id == cfg.complete_label_id:
        if buildings == 0:
            raise HTTPException(
                status_code=409, detail="Draw at least one building, or mark the scene Empty"
            )
        if unscored:
            raise HTTPException(
                status_code=409,
                detail=f"Score every building before completing ({unscored} unscored)",
            )
    elif label_id == cfg.empty_label_id:
        if buildings:
            raise HTTPException(
                status_code=409,
                detail=f"This scene has {buildings} building(s); delete them to mark it Empty",
            )
    else:
        raise _bad("A scene is finished with Complete, Empty or Skip")


def reopen_scenes(db: Session, campaign: Campaign, owners: set[tuple[UUID, int]]) -> list[int]:
    """Clear the outcome of each (user, scene) so an edited scene counts as a draft again,
    and withdraw those users' hand-ins. Returns the deleted outcome annotation ids, for
    the caller to tombstone. Does nothing outside building campaigns, where an explore
    shape's origin task is provenance only."""
    if not owners or building_scenes(campaign) is None:
        return []
    campaign_id = campaign.id
    outcomes = [
        a
        for a in db.scalars(
            select(Annotation).where(
                Annotation.campaign_id == campaign_id,
                Annotation.annotation_task_id.in_({task_id for _, task_id in owners}),
                Annotation.created_by_user_id.in_({user for user, _ in owners}),
            )
        ).all()
        if (a.created_by_user_id, a.annotation_task_id) in owners
    ]
    deleted = [a.id for a in outcomes]
    if outcomes:
        delete_rows_and_orphan_geometries(db, outcomes)
    withdraw_hand_ins(db, campaign_id, {user for user, _ in owners})
    return deleted


def withdraw_hand_ins(db: Session, campaign_id: int, user_ids: Iterable[UUID]) -> None:
    ids = set(user_ids)
    if ids:
        db.execute(
            delete(BuildingHandIn).where(
                BuildingHandIn.campaign_id == campaign_id, BuildingHandIn.user_id.in_(ids)
            )
        )


# ---------------------------------------------------------------------------
# Progress and hand-in
# ---------------------------------------------------------------------------


def user_scenes(db: Session, campaign_id: int, user_id: UUID) -> list[AnnotationTask]:
    """The scenes a user is responsible for: those assigned to them, or every scene of
    the campaign when nothing is assigned to them."""
    assigned = db.scalars(
        select(AnnotationTask)
        .join(AnnotationTaskAssignment, AnnotationTaskAssignment.task_id == AnnotationTask.id)
        .where(
            AnnotationTask.campaign_id == campaign_id,
            AnnotationTaskAssignment.user_id == user_id,
            AnnotationTaskAssignment.is_review.is_(False),
        )
        .order_by(AnnotationTask.annotation_number)
    ).all()
    if assigned:
        return list(assigned)
    return list(
        db.scalars(
            select(AnnotationTask)
            .where(AnnotationTask.campaign_id == campaign_id)
            .order_by(AnnotationTask.annotation_number)
        ).all()
    )


NO_OUTCOME = object()


def scene_state(cfg: BuildingScenes, outcome_label: object, buildings: int) -> SceneState:
    """``outcome_label`` is the label of the user's task annotation (None for a Skip),
    or ``NO_OUTCOME`` when there is none."""
    if outcome_label is NO_OUTCOME:
        return "draft" if buildings else "todo"
    if outcome_label == cfg.complete_label_id:
        return "complete"
    if outcome_label == cfg.empty_label_id:
        return "empty"
    return "skipped"


def scene_summaries(
    db: Session, campaign: Campaign, cfg: BuildingScenes, user_id: UUID
) -> list[SceneSummary]:
    scenes = user_scenes(db, campaign.id, user_id)
    ids = [s.id for s in scenes]
    outcomes: dict[int, int | None] = {}
    if ids:
        outcomes = dict(
            db.execute(
                select(Annotation.annotation_task_id, Annotation.label_id).where(
                    Annotation.annotation_task_id.in_(ids),
                    Annotation.created_by_user_id == user_id,
                )
            ).all()
        )
    counts = _building_counts(db, user_id, ids)
    summaries = []
    for s in scenes:
        buildings, unscored = counts.get(s.id, (0, 0))
        summaries.append(
            SceneSummary(
                task_id=s.id,
                annotation_number=s.annotation_number,
                state=scene_state(cfg, outcomes.get(s.id, NO_OUTCOME), buildings),
                buildings=buildings,
                unscored=unscored,
            )
        )
    return summaries


def hand_in_blockers(summaries: list[SceneSummary]) -> list[SceneSummary]:
    return [s for s in summaries if s.state not in FINISHED]


def get_hand_in(db: Session, campaign_id: int, user_id: UUID) -> BuildingHandIn | None:
    return db.scalar(
        select(BuildingHandIn).where(
            BuildingHandIn.campaign_id == campaign_id, BuildingHandIn.user_id == user_id
        )
    )


def revalidate_hand_in(db: Session, campaign: Campaign, user_id: UUID) -> None:
    """Withdraw the user's hand-in once one of their scenes is no longer finished."""
    cfg = building_scenes(campaign)
    if cfg is None:
        return
    # The session doesn't autoflush; the scene states below must see pending writes.
    db.flush()
    if get_hand_in(db, campaign.id, user_id) is None:
        return
    if hand_in_blockers(scene_summaries(db, campaign, cfg, user_id)):
        withdraw_hand_ins(db, campaign.id, [user_id])


def require_building_campaign(campaign: Campaign) -> BuildingScenes:
    cfg = building_scenes(campaign)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Not a building campaign")
    return cfg


def hand_in(db: Session, campaign: Campaign, user_id: UUID) -> None:
    """Record the hand-in; refused while any of the user's scenes is not finished."""
    cfg = require_building_campaign(campaign)
    summaries = scene_summaries(db, campaign, cfg, user_id)
    if not summaries:
        raise HTTPException(status_code=409, detail="There are no scenes to hand in")
    blockers = hand_in_blockers(summaries)
    if blockers:
        preview = ", ".join(f"#{s.annotation_number} ({s.state})" for s in blockers[:10])
        raise HTTPException(
            status_code=409,
            detail=f"{len(blockers)} scene(s) are not Complete or Empty: {preview}",
        )
    if get_hand_in(db, campaign.id, user_id) is None:
        db.add(BuildingHandIn(campaign_id=campaign.id, user_id=user_id))


def scene_buildings(
    db: Session, campaign: Campaign, task_id: int, user_id: UUID
) -> list[Annotation]:
    """One user's buildings in one scene, oldest first."""
    require_building_campaign(campaign)
    _scene_cells(db, campaign.id, [task_id])
    return list(
        db.scalars(
            select(Annotation)
            .where(
                Annotation.campaign_id == campaign.id,
                Annotation.origin_task_id == task_id,
                Annotation.annotation_task_id.is_(None),
                Annotation.created_by_user_id == user_id,
            )
            .options(joinedload(Annotation.geometry), joinedload(Annotation.creator))
            .order_by(Annotation.id)
        ).unique()
    )


def scene_building_count(db: Session, campaign_id: int, task_ids: Iterable[int]) -> int:
    """Buildings (any user) drawn in these scenes."""
    ids = list(task_ids)
    if not ids:
        return 0
    return (
        db.scalar(
            select(func.count())
            .select_from(Annotation)
            .where(
                Annotation.campaign_id == campaign_id,
                Annotation.origin_task_id.in_(ids),
                Annotation.annotation_task_id.is_(None),
            )
        )
        or 0
    )

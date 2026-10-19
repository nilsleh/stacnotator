"""Building-campaign operations behind the /buildings routes. Writes commit.

Clearing a scene's outcome (back to Draft) is deleting the user's task annotation
through the regular annotation endpoint, whose hooks revalidate the hand-in."""

from collections import defaultdict
from typing import get_args
from uuid import UUID

from geoalchemy2.shape import to_shape
from shapely import STRtree
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.annotation.models import Annotation, AnnotationGeometry, AnnotationTask
from src.buildings import evaluation, lifecycle
from src.buildings.schemas import (
    BuildingProgressOut,
    BuildingsEvaluationOut,
    HandInOut,
    SceneProgressOut,
)
from src.campaigns.models import Campaign


def progress(db: Session, campaign: Campaign, user_id: UUID) -> BuildingProgressOut:
    cfg = lifecycle.require_building_campaign(campaign)
    summaries = lifecycle.scene_summaries(db, campaign, cfg, user_id)
    counts = dict.fromkeys(get_args(lifecycle.SceneState), 0)
    for s in summaries:
        counts[s.state] += 1
    hand_in = lifecycle.get_hand_in(db, campaign.id, user_id)
    return BuildingProgressOut(
        scenes=[SceneProgressOut(**s.__dict__) for s in summaries],
        counts=counts,
        hand_in=HandInOut(
            handed_in_at=hand_in.handed_in_at if hand_in else None,
            can_hand_in=bool(summaries) and not lifecycle.hand_in_blockers(summaries),
        ),
    )


def hand_in(db: Session, campaign: Campaign, user_id: UUID) -> BuildingProgressOut:
    lifecycle.hand_in(db, campaign, user_id)
    db.commit()
    return progress(db, campaign, user_id)


def withdraw_hand_in(db: Session, campaign: Campaign, user_id: UUID) -> BuildingProgressOut:
    lifecycle.require_building_campaign(campaign)
    lifecycle.withdraw_hand_ins(db, campaign.id, [user_id])
    db.commit()
    return progress(db, campaign, user_id)


def evaluate(
    db: Session,
    campaign: Campaign,
    user_id: UUID,
    split: str | None = None,
    threshold: float = evaluation.DEFAULT_IOU_THRESHOLD,
) -> BuildingsEvaluationOut:
    """Score one annotator's Complete and Empty scenes (optionally one split) against the
    campaign's references. A reference belongs to the scene its interior lies in: the
    seeder clips each one to its chip."""
    cfg = lifecycle.require_building_campaign(campaign)
    finished = {
        s.task_id: s.state
        for s in lifecycle.scene_summaries(db, campaign, cfg, user_id)
        if s.state in lifecycle.FINISHED
    }
    tasks = [
        t
        for t in db.scalars(
            select(AnnotationTask)
            .where(AnnotationTask.id.in_(finished))
            .options(joinedload(AnnotationTask.geometry))
            .order_by(AnnotationTask.annotation_number)
        )
        if split is None or _split(t) == split
    ]
    task_ids = [t.id for t in tasks]

    buildings: dict[int, list[evaluation.Shape]] = defaultdict(list)
    for annotation_id, task_id, geom in db.execute(
        select(Annotation.id, Annotation.origin_task_id, AnnotationGeometry.geometry)
        .join(AnnotationGeometry, AnnotationGeometry.id == Annotation.geometry_id)
        .where(
            Annotation.origin_task_id.in_(task_ids),
            Annotation.annotation_task_id.is_(None),
            Annotation.source_id.is_(None),
            Annotation.created_by_user_id == user_id,
        )
    ):
        buildings[task_id].append((annotation_id, to_shape(geom)))

    references: dict[int, list[evaluation.Shape]] = defaultdict(list)
    if tasks:
        cells = STRtree([to_shape(t.geometry.geometry) for t in tasks])
        for annotation_id, geom in db.execute(
            select(Annotation.id, AnnotationGeometry.geometry)
            .join(AnnotationGeometry, AnnotationGeometry.id == Annotation.geometry_id)
            .where(Annotation.campaign_id == campaign.id, Annotation.source_id.is_not(None))
        ):
            shape = to_shape(geom)
            for i in cells.query(shape.representative_point(), predicate="within")[:1]:
                references[tasks[int(i)].id].append((annotation_id, shape))

    scenes = [
        evaluation.SceneInput(
            task_id=t.id,
            annotation_number=t.annotation_number,
            split=_split(t),
            outcome=finished[t.id],
            buildings=buildings[t.id],
            references=references[t.id],
        )
        for t in tasks
    ]
    result = evaluation.evaluate_scenes(scenes, threshold)
    return result.model_copy(
        update={"campaign_id": campaign.id, "user_id": user_id, "split": split}
    )


def _split(task: AnnotationTask) -> str | None:
    return (task.raw_source_data or {}).get("split")

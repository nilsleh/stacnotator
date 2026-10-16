"""Building-campaign operations behind the /buildings routes. Each one commits."""

from typing import get_args
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.annotation import service as annotation_service
from src.annotation.geometries import delete_rows_and_orphan_geometries
from src.annotation.models import Annotation, AnnotationTask
from src.buildings import lifecycle
from src.buildings.schemas import BuildingProgressOut, HandInOut, SceneProgressOut
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


def mark_draft(db: Session, campaign: Campaign, task_id: int, user_id: UUID) -> BuildingProgressOut:
    """Clear the user's outcome on a scene, leaving its buildings as a draft."""
    lifecycle.require_building_campaign(campaign)
    task = db.scalar(
        select(AnnotationTask).where(
            AnnotationTask.id == task_id, AnnotationTask.campaign_id == campaign.id
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Scene not found in this campaign")
    outcome = db.scalar(
        select(Annotation).where(
            Annotation.annotation_task_id == task_id, Annotation.created_by_user_id == user_id
        )
    )
    if outcome is not None:
        outcome_id = outcome.id
        delete_rows_and_orphan_geometries(db, [outcome])
        annotation_service.record_annotation_deletions(db, campaign.id, [outcome_id])
        annotation_service.bump_campaign_annotations_version(db, campaign.id)
        lifecycle.revalidate_hand_in(db, campaign, user_id)
        db.commit()
    return progress(db, campaign, user_id)

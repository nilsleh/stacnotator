"""Building scenes worked by an agent, through the same rules a person's scenes go through.

Buildings are created and deleted by the annotation service (whose building hooks check
the label, scene, confidence, geometry and hand-in), a scene's outcome is a task
submission, and hand-in is the buildings service's. An agent only ever reaches the
scenes assigned to it, and nothing here returns references.
"""

from uuid import UUID

from fastapi import HTTPException
from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.models import LabellingAgent
from src.agents.schemas import AgentBuildingOut, AgentBuildingsCreate, AgentSceneOutcome
from src.annotation import service as annotation_service
from src.annotation.models import AnnotationTask, AnnotationTaskAssignment
from src.annotation.schemas import (
    AnnotationCreate,
    AnnotationFromTaskCreate,
    AnnotationTaskSubmitResponse,
)
from src.buildings import lifecycle
from src.buildings import service as buildings_service
from src.buildings.config import building_scenes
from src.buildings.schemas import BuildingProgressOut
from src.campaigns.models import Campaign


def _assigned(agent: LabellingAgent):
    return (
        select(AnnotationTask)
        .join(AnnotationTaskAssignment, AnnotationTaskAssignment.task_id == AnnotationTask.id)
        .where(
            AnnotationTask.campaign_id == agent.campaign_id,
            AnnotationTaskAssignment.user_id == agent.user_id,
            ~AnnotationTaskAssignment.is_review,
        )
    )


def _require_scene(db: Session, agent: LabellingAgent, campaign: Campaign, task_id: int) -> None:
    lifecycle.require_building_campaign(campaign)
    if db.scalar(_assigned(agent).where(AnnotationTask.id == task_id)) is None:
        raise HTTPException(status_code=404, detail="Scene not assigned to this agent")


def buildings_out(
    db: Session, campaign: Campaign, task_id: int, user_id: UUID
) -> list[AgentBuildingOut]:
    return [
        AgentBuildingOut(
            building_id=a.id,
            geometry_wkt=to_shape(a.geometry.geometry).wkt,
            confidence=a.confidence,
        )
        for a in lifecycle.scene_buildings(db, campaign, task_id, user_id)
    ]


def draw(
    db: Session, agent: LabellingAgent, campaign: Campaign, task_id: int, body: AgentBuildingsCreate
) -> list[AgentBuildingOut]:
    _require_scene(db, agent, campaign, task_id)
    cfg = lifecycle.require_building_campaign(campaign)
    annotation_service.create_annotations_bulk(
        db,
        campaign,
        [
            AnnotationCreate(
                label_id=cfg.building_label_id,
                comment=None,
                geometry_wkt=b.geometry_wkt,
                confidence=b.confidence,
                origin_task_id=task_id,
            )
            for b in body.buildings
        ],
        agent.user_id,
    )
    return buildings_out(db, campaign, task_id, agent.user_id)


def delete(
    db: Session, agent: LabellingAgent, campaign: Campaign, task_id: int, building_ids: list[int]
) -> list[AgentBuildingOut]:
    _require_scene(db, agent, campaign, task_id)
    own = {b.building_id for b in buildings_out(db, campaign, task_id, agent.user_id)}
    foreign = set(building_ids) - own
    if foreign:
        raise HTTPException(
            status_code=404, detail=f"Not this agent's buildings in this scene: {sorted(foreign)}"
        )
    annotation_service.delete_annotations_bulk(db, building_ids, campaign, agent.user_id)
    return buildings_out(db, campaign, task_id, agent.user_id)


def set_outcome(
    db: Session, agent: LabellingAgent, campaign: Campaign, task_id: int, body: AgentSceneOutcome
) -> AnnotationTaskSubmitResponse:
    _require_scene(db, agent, campaign, task_id)
    cfg = lifecycle.require_building_campaign(campaign)
    label_id = cfg.complete_label_id if body.outcome == "complete" else cfg.empty_label_id
    return annotation_service.submit_task_annotation(
        db=db,
        campaign=campaign,
        task_id=task_id,
        annotation_create=AnnotationFromTaskCreate(label_id=label_id, comment=body.comment),
        user_id=agent.user_id,
    )


def hand_in(db: Session, agent: LabellingAgent, campaign: Campaign) -> BuildingProgressOut:
    return buildings_service.hand_in(db, campaign, agent.user_id)


def assign_scenes(db: Session, agent: LabellingAgent, count: int, split: str | None) -> int:
    """Give the agent its own copy of up to ``count`` scenes, in scene order.

    A building scene is labelled by everyone it is assigned to, each scored on their own,
    so an agent takes scenes alongside whoever else has them rather than from a pool.
    """
    taken = select(AnnotationTaskAssignment.task_id).where(
        AnnotationTaskAssignment.user_id == agent.user_id
    )
    query = select(AnnotationTask.id).where(
        AnnotationTask.campaign_id == agent.campaign_id, AnnotationTask.id.not_in(taken)
    )
    if split is not None:
        query = query.where(AnnotationTask.raw_source_data["split"].astext == split)
    task_ids = db.scalars(query.order_by(AnnotationTask.annotation_number).limit(count)).all()
    db.add_all(AnnotationTaskAssignment(task_id=t, user_id=agent.user_id) for t in task_ids)
    db.commit()
    return len(task_ids)


def is_building_campaign(campaign: Campaign) -> bool:
    return building_scenes(campaign) is not None

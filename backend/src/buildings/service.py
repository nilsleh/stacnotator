"""Building-campaign operations behind the /buildings routes. Each one commits.

Clearing a scene's outcome (back to Draft) is deleting the user's task annotation
through the regular annotation endpoint, whose hooks revalidate the hand-in."""

from typing import get_args
from uuid import UUID

from sqlalchemy.orm import Session

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

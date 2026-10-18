from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from src.annotation.models import Annotation
from src.annotation.schemas import AnnotationOut
from src.auth.dependencies import require_authenticated_user
from src.auth.models import User
from src.buildings import lifecycle, service
from src.buildings.schemas import BuildingProgressOut
from src.campaigns.dependencies import require_campaign_access
from src.campaigns.models import Campaign
from src.database import get_db

bearer = HTTPBearer()  # Using only for adding bearer scheme to Swagger OpenAPI
router = APIRouter(
    tags=["Buildings"],
    dependencies=[Depends(bearer), Depends(require_authenticated_user)],
)


@router.get("/campaigns/{campaign_id}/buildings/progress", response_model=BuildingProgressOut)
def get_building_progress(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_access),
) -> BuildingProgressOut:
    """The caller's scenes (Complete / Empty / Skipped / Draft / To do) and hand-in."""
    return service.progress(db, campaign, user.id)


@router.post("/campaigns/{campaign_id}/buildings/hand-in", response_model=BuildingProgressOut)
def hand_in_buildings(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_access),
) -> BuildingProgressOut:
    """Hand in the caller's scenes; 409 while any is not Complete or Empty."""
    return service.hand_in(db, campaign, user.id)


@router.delete("/campaigns/{campaign_id}/buildings/hand-in", response_model=BuildingProgressOut)
def withdraw_building_hand_in(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_access),
) -> BuildingProgressOut:
    return service.withdraw_hand_in(db, campaign, user.id)


@router.get(
    "/campaigns/{campaign_id}/buildings/scenes/{task_id}/buildings",
    response_model=list[AnnotationOut],
)
def get_scene_buildings(
    campaign_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_access),
) -> list[Annotation]:
    """The caller's buildings in one scene, oldest first."""
    return lifecycle.scene_buildings(db, campaign, task_id, user.id)

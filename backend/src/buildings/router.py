from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session

from src.annotation import spatial
from src.annotation.models import Annotation
from src.annotation.schemas import AnnotationOut
from src.annotation.tiles import InvalidTileError
from src.auth.dependencies import require_authenticated_user
from src.auth.models import User
from src.buildings import lifecycle, service
from src.buildings.evaluation import DEFAULT_IOU_THRESHOLD
from src.buildings.schemas import BuildingProgressOut, BuildingsEvaluationOut
from src.campaigns.dependencies import require_campaign_access, require_campaign_admin
from src.campaigns.models import Campaign
from src.database import get_db
from src.tile_bulkhead import tile_slot

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


@router.get(
    "/campaigns/{campaign_id}/buildings/references/tiles/{z}/{x}/{y}.pbf",
    dependencies=[Depends(tile_slot)],
)
def get_reference_tile(
    campaign_id: int,
    z: int,
    x: int,
    y: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(require_campaign_admin),
) -> Response:
    """The campaign's imported reference footprints as a vector tile, for its admins.

    Every other read leaves them out (src.buildings.references); this is the map's
    read-only reference layer.
    """
    lifecycle.require_building_campaign(campaign)
    try:
        tile = spatial.render_annotation_tile(db, campaign.id, z, x, y, references=True)
    except InvalidTileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/campaigns/{campaign_id}/buildings/evaluation", response_model=BuildingsEvaluationOut)
def evaluate_buildings(
    campaign_id: int,
    user_id: UUID | None = None,
    split: str | None = None,
    iou_threshold: float = Query(DEFAULT_IOU_THRESHOLD, gt=0, le=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_authenticated_user),
    campaign: Campaign = Depends(require_campaign_admin),
) -> BuildingsEvaluationOut:
    """Score an annotator's Complete and Empty scenes against the reference footprints
    (campaign admins; ``user_id`` defaults to the caller, ``split`` to every split)."""
    return service.evaluate(db, campaign, user_id or user.id, split, iou_threshold)

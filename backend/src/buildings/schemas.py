from datetime import datetime

from pydantic import BaseModel

from src.buildings.lifecycle import SceneState


class SceneProgressOut(BaseModel):
    task_id: int
    annotation_number: int
    state: SceneState
    buildings: int
    unscored: int


class HandInOut(BaseModel):
    handed_in_at: datetime | None
    # True once every scene is Complete or Empty (and none is left to hand in).
    can_hand_in: bool


class BuildingProgressOut(BaseModel):
    """The requesting user's scenes in a building campaign, in task order."""

    scenes: list[SceneProgressOut]
    counts: dict[SceneState, int]
    hand_in: HandInOut

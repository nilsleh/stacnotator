from datetime import datetime
from uuid import UUID

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


class BuildingMetricsOut(BaseModel):
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    # Over matched pairs only.
    mean_iou: float
    predicted_count: int
    reference_count: int
    # Buildings drawn minus references: negative when buildings were missed.
    count_error: int
    iou_threshold: float


class BuildingMatchOut(BaseModel):
    building_id: int
    reference_id: int
    iou: float


class SceneEvaluationOut(BaseModel):
    task_id: int
    annotation_number: int
    split: str | None
    # The annotator's outcome: only Complete and Empty scenes are scored.
    outcome: str
    metrics: BuildingMetricsOut
    matches: list[BuildingMatchOut]
    false_positive_ids: list[int]
    false_negative_ids: list[int]


class BuildingsEvaluationOut(BaseModel):
    """One annotator's finished scenes scored against the campaign's references."""

    campaign_id: int | None = None
    user_id: UUID | None = None
    split: str | None = None
    iou_threshold: float
    overall: BuildingMetricsOut
    by_split: dict[str, BuildingMetricsOut]
    by_size_bucket: dict[str, BuildingMetricsOut]
    empty_scenes: int
    empty_scenes_with_false_positives: int
    empty_scene_fpr: float
    scenes: list[SceneEvaluationOut]

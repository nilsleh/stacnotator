"""Which labels a building campaign uses, stored in ``settings.building_scenes``."""

from pydantic import BaseModel, model_validator

# Every building is scored 1-5 when it is finished (stored in ``confidence``).
MIN_CONFIDENCE = 1
MAX_CONFIDENCE = 5


class BuildingScenes(BaseModel):
    """The scene outcome is the label of the user's task annotation, as with any task:
    Complete and Empty are labels (so they count as done), Skip is no label, and Draft is
    no annotation yet. Building polygons carry ``building_label_id``."""

    building_label_id: int
    complete_label_id: int
    empty_label_id: int

    @model_validator(mode="after")
    def _distinct(self) -> "BuildingScenes":
        ids = {self.building_label_id, self.complete_label_id, self.empty_label_id}
        if len(ids) != 3:
            raise ValueError("building, complete and empty labels must differ")
        return self

    def check_labels(self, labels: dict) -> None:
        """``labels`` is the settings JSONB (``{"1": {"name", "geometry_type"}}``)."""
        building = labels.get(str(self.building_label_id))
        if not building or building.get("geometry_type") != "polygon":
            raise ValueError("building_label_id must be a polygon label of the campaign")
        for label_id in (self.complete_label_id, self.empty_label_id):
            outcome = labels.get(str(label_id))
            if not outcome or outcome.get("geometry_type") is not None:
                raise ValueError("scene outcome labels must be campaign labels without geometry")


def building_scenes(campaign) -> BuildingScenes | None:
    """The campaign's building configuration, or None for any other campaign."""
    settings = getattr(campaign, "settings", None)
    raw = getattr(settings, "building_scenes", None)
    return BuildingScenes.model_validate(raw) if isinstance(raw, dict) and raw else None

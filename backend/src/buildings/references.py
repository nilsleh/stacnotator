"""Imported reference footprints are held out of a building campaign's ordinary reads.

References carry a ``source_id``. Annotators' buildings are scored against them
(``src.buildings.evaluation``), which only means something while nobody labelling can see
them, so the map tiles, box selection, extent, density, the changes poll, the annotation
list and single-annotation reads all leave them out (the raw-SQL half is
``HELD_OUT_REFERENCES_SQL`` in ``annotation/tiles.py``). Campaign admins reach them
through their own tile route and the ``references`` export. Other campaigns are
unaffected.
"""

from sqlalchemy import ColumnElement, true
from sqlalchemy.orm import Session

from src.annotation.models import Annotation
from src.auth.models import User
from src.buildings.config import building_scenes
from src.campaigns.dependencies import require_campaign_admin
from src.campaigns.models import Campaign


def visible_annotations(campaign: Campaign) -> ColumnElement[bool]:
    """ORM filter for the annotations an ordinary read of ``campaign`` may return."""
    return Annotation.source_id.is_(None) if building_scenes(campaign) is not None else true()


def require_reference_access(db: Session, campaign: Campaign, user: User) -> None:
    """References of a building campaign are for its admins only (403 otherwise)."""
    if building_scenes(campaign) is not None:
        require_campaign_admin(campaign_id=campaign.id, db=db, user=user)

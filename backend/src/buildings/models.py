from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Identity, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class BuildingHandIn(Base):
    """A user's statement that their scenes in a building campaign are finished.

    Only recorded while every scene assigned to them is Complete or Empty. It locks
    those scenes: the user withdraws it (the row is deleted) before changing any of
    them. A change made by someone else, such as an admin, withdraws it instead.
    """

    __tablename__ = "building_hand_ins"
    __table_args__ = (
        UniqueConstraint("campaign_id", "user_id", name="uq_building_hand_in_campaign_user"),
        {"schema": "data"},
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("data.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    handed_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BuildingSceneSource(Base):
    """Trusted import provenance, never included in execution task/context responses."""

    __tablename__ = "building_scene_sources"
    __table_args__ = {"schema": "data"}

    task_id: Mapped[int] = mapped_column(
        ForeignKey("data.annotation_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    source_chip_id: Mapped[str] = mapped_column(Text)
    raster_metadata: Mapped[dict] = mapped_column(JSON)

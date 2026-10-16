from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Identity, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class BuildingHandIn(Base):
    """A user's statement that their scenes in a building campaign are finished.

    Only recorded while every scene assigned to them is Complete or Empty; any later
    change to one of their scenes withdraws it (the row is deleted).
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

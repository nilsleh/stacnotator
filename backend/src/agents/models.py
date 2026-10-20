from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Text, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.auth.models import User
from src.database import Base


class LabellingAgent(Base):
    """An automated annotator working a campaign on behalf of a user.

    The agent is a user row of its own, so assignments, annotations, statistics
    and the review page treat it like any other annotator. This row only adds
    who runs it.
    """

    __tablename__ = "labelling_agents"
    __table_args__ = (
        Index("idx_labelling_agents_owner_campaign", "owner_user_id", "campaign_id"),
        {"schema": "data"},
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth.users.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("data.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When its own tasks run out, take over tasks still waiting on its owner's other
    # agents in the campaign.
    takes_over_work: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class AgentOperation(Base):
    """Successful responses retained for the lifetime of the campaign/principal."""

    __tablename__ = "agent_operations"
    __table_args__ = {"schema": "data"}

    principal_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True
    )
    target: Mapped[str] = mapped_column(Text, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("data.campaigns.id", ondelete="CASCADE"), nullable=False
    )
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[object] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRender(Base):
    __tablename__ = "agent_renders"
    __table_args__ = {"schema": "data"}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("data.labelling_agents.user_id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[int] = mapped_column(ForeignKey("data.annotation_tasks.id", ondelete="CASCADE"))
    image_sha256: Mapped[str] = mapped_column(Text)
    image_metadata: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AgentCredential(Base):
    __tablename__ = "agent_credentials"
    __table_args__ = {"schema": "data"}

    id: Mapped[UUID] = mapped_column(primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("data.labelling_agents.user_id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

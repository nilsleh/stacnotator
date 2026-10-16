"""Building campaigns: scene configuration and per-user hand-ins.

Revision ID: bb1buildings
Revises: at1mapaccess
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "bb1buildings"
down_revision: str | Sequence[str] | None = "at1mapaccess"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("building_scenes", postgresql.JSONB(), nullable=True),
        schema="data",
    )
    op.create_table(
        "building_hand_ins",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("data.campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "handed_in_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("campaign_id", "user_id", name="uq_building_hand_in_campaign_user"),
        schema="data",
    )


def downgrade() -> None:
    op.drop_table("building_hand_ins", schema="data")
    op.drop_column("settings", "building_scenes", schema="data")

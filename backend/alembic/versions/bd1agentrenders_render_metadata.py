"""Expiring agent render references, without image storage."""

import sqlalchemy as sa

from alembic import op

revision = "bd1agentrenders"
down_revision = "bc1agentops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_renders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("data.labelling_agents.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("data.annotation_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_sha256", sa.Text(), nullable=False),
        sa.Column("image_metadata", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="data",
    )
    op.create_index("ix_data_agent_renders_agent_id", "agent_renders", ["agent_id"], schema="data")
    op.create_index(
        "ix_data_agent_renders_expires_at", "agent_renders", ["expires_at"], schema="data"
    )


def downgrade() -> None:
    op.drop_table("agent_renders", schema="data")

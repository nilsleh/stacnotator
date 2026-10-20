"""Expiring and revocable single-agent execution credentials."""

import sqlalchemy as sa

from alembic import op

revision = "be1agentcreds"
down_revision = "bb1buildings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "agent_id",
            sa.Uuid(),
            sa.ForeignKey("data.labelling_agents.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema="data",
    )
    op.create_index(
        "ix_data_agent_credentials_agent_id", "agent_credentials", ["agent_id"], schema="data"
    )


def downgrade() -> None:
    op.drop_table("agent_credentials", schema="data")

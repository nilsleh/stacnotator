"""Atomic agent mutation receipts.

Revision ID: bc1agentops
Revises: bb1buildings
"""

import sqlalchemy as sa

from alembic import op

revision = "bc1agentops"
down_revision = "bb1buildings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_operations",
        sa.Column(
            "principal_id",
            sa.Uuid(),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target", sa.Text(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("data.campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="data",
    )


def downgrade() -> None:
    op.drop_table("agent_operations", schema="data")

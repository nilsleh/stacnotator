"""Trusted chip identity and reference-to-scene provenance."""

import sqlalchemy as sa

from alembic import op

revision = "bf1sceneprovenance"
down_revision = "be1agentcreds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "building_scene_sources",
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("data.annotation_tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("source_chip_id", sa.Text(), nullable=False),
        sa.Column("raster_metadata", sa.JSON(), nullable=False),
        schema="data",
    )
    op.add_column(
        "annotations", sa.Column("reference_task_id", sa.Integer(), nullable=True), schema="data"
    )
    op.create_foreign_key(
        "fk_annotations_reference_task",
        "annotations",
        "annotation_tasks",
        ["reference_task_id"],
        ["id"],
        source_schema="data",
        referent_schema="data",
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_annotations_reference_task_id", "annotations", ["reference_task_id"], schema="data"
    )
    # Backfill only an unambiguous containing scene across the entire campaign,
    # never just the scenes a particular annotator finished.
    op.execute("""
        WITH candidates AS (
            SELECT a.id, min(t.id) AS task_id
            FROM data.annotations a
            JOIN data.settings s ON s.campaign_id = a.campaign_id
            JOIN data.annotation_geometries ag ON ag.id = a.geometry_id
            JOIN data.annotation_tasks t ON t.campaign_id = a.campaign_id
            JOIN data.annotation_geometries tg ON tg.id = t.geometry_id
            WHERE a.source_id IS NOT NULL AND jsonb_typeof(s.building_scenes) = 'object'
              AND ST_Covers(tg.geometry, ag.geometry)
            GROUP BY a.id HAVING count(*) = 1
        )
        UPDATE data.annotations a SET reference_task_id = c.task_id
        FROM candidates c WHERE a.id = c.id
    """)


def downgrade() -> None:
    op.drop_column("annotations", "reference_task_id", schema="data")
    op.drop_table("building_scene_sources", schema="data")

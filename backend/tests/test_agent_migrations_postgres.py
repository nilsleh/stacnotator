"""Validate upgrades/backfill on a disposable database, never the configured database."""

import os
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select, text
from sqlalchemy.orm import Session

from alembic import command
from src import config
from src.annotation.models import AnnotationGeometry, AnnotationTask
from src.auth.models import User
from src.campaigns.models import Campaign, CampaignSettings, TaskSet
from src.organizations.models import Organization
from src.projects.models import Project

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="requires opt-in real Postgres"
)


def test_agent_migrations_upgrade_backfill_and_downgrade(monkeypatch):
    settings = config.get_settings()
    name = f"agent_migration_test_{uuid4().hex}"
    admin = create_engine(settings.DATABASE_URL, isolation_level="AUTOCOMMIT")
    isolated_settings = settings.model_copy(update={"DBNAME": name})
    engine = create_engine(isolated_settings.DATABASE_URL)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        monkeypatch.setattr(config, "get_settings", lambda: isolated_settings)
        # Avoid replacing pytest's logging configuration via Alembic's fileConfig.
        alembic = Config()
        alembic.set_main_option("script_location", "alembic")
        command.upgrade(alembic, "bb1buildings")
        with Session(engine) as db:
            owner = User(id=uuid4(), issuer="test", external_uid="migration", email="test@invalid")
            campaign = Campaign(
                name="migration",
                mode="tasks",
                project=Project(
                    name="migration", organization=Organization(name=name, status="approved")
                ),
                settings=CampaignSettings(
                    bbox_west=0,
                    bbox_south=0,
                    bbox_east=10,
                    bbox_north=10,
                    building_scenes={
                        "building_label_id": 1,
                        "complete_label_id": 2,
                        "empty_label_id": 3,
                    },
                ),
            )
            db.add_all([owner, campaign])
            db.flush()
            task_set = TaskSet(campaign_id=campaign.id, name="scenes")
            db.add(task_set)
            db.flush()
            tasks = []
            for i, wkt in enumerate(
                [
                    "POLYGON((0 0,2 0,2 2,0 2,0 0))",
                    "POLYGON((3 3,5 3,5 5,3 5,3 3))",
                    "POLYGON((3 3,5 3,5 5,3 5,3 3))",
                ],
                start=1,
            ):
                task = AnnotationTask(
                    campaign_id=campaign.id,
                    task_set_id=task_set.id,
                    annotation_number=i,
                    geometry=AnnotationGeometry(geometry=f"SRID=4326;{wkt}"),
                )
                db.add(task)
                tasks.append(task)
            db.flush()
            annotations = Table("annotations", MetaData(), schema="data", autoload_with=engine)
            ids = []
            for source_id, wkt in enumerate(
                [
                    "POLYGON((0 0,1 0,1 1,0 1,0 0))",
                    "POLYGON((3 3,4 3,4 4,3 4,3 3))",
                    "POLYGON((8 8,9 8,9 9,8 9,8 8))",
                ],
                start=1,
            ):
                geom = AnnotationGeometry(geometry=f"SRID=4326;{wkt}")
                db.add(geom)
                db.flush()
                ids.append(
                    db.scalar(
                        annotations.insert()
                        .values(
                            campaign_id=campaign.id,
                            geometry_id=geom.id,
                            created_by_user_id=owner.id,
                            source_id=source_id,
                            label_id=1,
                        )
                        .returning(annotations.c.id)
                    )
                )
            task_id = tasks[0].id
            db.commit()
        command.upgrade(alembic, "head")
        tables = inspect(engine).get_table_names(schema="data")
        assert {"agent_credentials", "building_scene_sources"} <= set(tables)
        assert not {"agent_operations", "agent_renders"}.intersection(tables)
        annotations = Table("annotations", MetaData(), schema="data", autoload_with=engine)
        with engine.connect() as connection:
            provenance = dict(
                connection.execute(select(annotations.c.id, annotations.c.reference_task_id)).all()
            )
            assert provenance == {ids[0]: task_id, ids[1]: None, ids[2]: None}
        command.downgrade(alembic, "bb1buildings")
        assert "reference_task_id" not in {
            c["name"] for c in inspect(engine).get_columns("annotations", schema="data")
        }
        command.upgrade(alembic, "head")
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()

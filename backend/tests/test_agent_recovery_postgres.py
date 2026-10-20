"""Run with RUN_POSTGRES_TESTS=1; uses isolated schemas, never existing campaign data."""

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from shapely.geometry import box
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from src.agents import buildings, credentials, router, service
from src.agents.models import AgentCredential, LabellingAgent
from src.agents.schemas import (
    AgentAnnotate,
    AgentBuilding,
    AgentBuildingsCreate,
    AgentBuildingsDelete,
    AgentRegister,
    AgentSceneOutcome,
)
from src.annotation.models import Annotation, AnnotationGeometry, AnnotationTask
from src.auth.models import User, UserRole
from src.campaigns.models import Campaign, CampaignSettings, TaskSet
from src.config import get_settings
from src.database import Base, get_db
from src.organizations.models import Organization
from src.projects.models import Project

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="requires opt-in real Postgres"
)


@pytest.fixture(scope="module")
def engine():
    base = create_engine(get_settings().DATABASE_URL)
    suffix = uuid4().hex
    schemas = {"auth": f"agent_test_auth_{suffix}", "data": f"agent_test_data_{suffix}"}
    isolated = base.execution_options(schema_translate_map=schemas)
    with base.begin() as connection:
        for name in schemas.values():
            connection.execute(CreateSchema(name))
    try:
        Base.metadata.create_all(isolated)
        yield isolated
    finally:
        with base.begin() as connection:
            for name in reversed(list(schemas.values())):
                connection.execute(DropSchema(name, cascade=True))
        base.dispose()


@pytest.fixture
def setup(engine):
    with Session(engine) as db:
        uid = uuid4()
        owner = User(
            id=uid,
            issuer="test",
            external_uid=str(uid),
            email=f"{uid}@test.invalid",
            roles=[UserRole(role="admin")],
        )
        org = Organization(name=str(uid), status="approved")
        project = Project(name="test", organization=org)
        campaign = Campaign(
            name="test",
            mode="tasks",
            project=project,
            settings=CampaignSettings(
                bbox_west=0,
                bbox_south=0,
                bbox_east=1,
                bbox_north=1,
                labels={
                    "1": {"name": "Building", "geometry_type": "polygon"},
                    "2": {"name": "Complete"},
                    "3": {"name": "Empty"},
                },
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
        tasks = [
            AnnotationTask(
                campaign_id=campaign.id,
                task_set_id=task_set.id,
                annotation_number=i,
                geometry=AnnotationGeometry(geometry=f"SRID=4326;{box(0, 0, 0.01, 0.01).wkt}"),
            )
            for i in (1, 2)
        ]
        db.add_all(tasks)
        db.commit()
        return owner.id, campaign.id, [t.id for t in tasks]


def register(engine, setup, body):
    owner_id, campaign_id, _ = setup
    with Session(engine) as db:
        return router.register_agent(
            body, db, db.get(User, owner_id), db.get(Campaign, campaign_id)
        )


def draw(engine, setup, agent_id, body):
    owner_id, _, tasks = setup
    with Session(engine) as db:
        return router.draw_agent_buildings(agent_id, tasks[0], body, db, db.get(User, owner_id))


def sketch():
    return AgentBuildingsCreate(
        buildings=[AgentBuilding(geometry_wkt=box(0.001, 0.001, 0.002, 0.002).wkt, confidence=4)],
    )


def test_saved_registration_and_buildings_can_be_reconciled_in_a_new_session(engine, setup):
    agent = register(engine, setup, AgentRegister(name="recovery", task_count=2))
    drawn = draw(engine, setup, agent.agent_id, sketch())
    with Session(engine) as db:
        owner = db.get(User, setup[0])
        overview = router.list_agents(db, owner, db.get(Campaign, setup[1]))
        assert [a.agent_id for a in overview.agents] == [agent.agent_id]
        assert overview.agents[0].assigned == 2
        scene = router.get_agent_scene(agent.agent_id, setup[2][0], db, owner)
        assert scene.task.buildings == drawn
        assert scene.progress.state == "draft"


@pytest.mark.parametrize("building_campaign", [True, False])
def test_registration_assignment_failure_rolls_back(engine, setup, monkeypatch, building_campaign):
    module = buildings if building_campaign else service
    function = "assign_scenes" if building_campaign else "assign_tasks"
    original = getattr(module, function)
    body = AgentRegister(name="rollback")
    if not building_campaign:
        with Session(engine) as db:
            db.get(Campaign, setup[1]).settings.building_scenes = None
            db.commit()

    def fail(*args):
        original(*args)
        raise RuntimeError("assignment failure")

    with monkeypatch.context() as patch:
        patch.setattr(module, function, fail)
        with pytest.raises(RuntimeError, match="assignment failure"):
            register(engine, setup, body)
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(LabellingAgent)
                .where(LabellingAgent.campaign_id == setup[1])
            )
            == 0
        )
    assert register(engine, setup, body).assigned == 2


def test_campaign_assignment_route_commits(engine, setup):
    from src.campaigns import router as campaign_router
    from src.campaigns.schemas import AssignTasksToUsersRequest

    agent = register(engine, setup, AgentRegister(name="assignment", task_count=0))
    with Session(engine) as db:
        result = campaign_router.assign_tasks_to_users(
            setup[1],
            AssignTasksToUsersRequest(
                strategy="fixed_per_user", user_task_counts={agent.agent_id: 1}
            ),
            db,
            db.get(Campaign, setup[1]),
        )
        assert result.total_assigned == 1
    with Session(engine) as db:
        assert router.get_agent(agent.agent_id, db, db.get(User, setup[0])).assigned == 1


def test_scene_resume_progress_and_hand_in_locks(engine, setup):
    agent = register(engine, setup, AgentRegister(name="scenes", task_count=2))
    owner_id, _, tasks = setup
    with Session(engine) as db:
        owner = db.get(User, owner_id)
        assert router.agent_building_progress(agent.agent_id, db, owner).counts["todo"] == 2
        router.annotate_agent_task(
            agent.agent_id,
            tasks[1],
            AgentAnnotate(label_id=None, comment="skip"),
            db,
            owner,
        )
        progress = router.agent_building_progress(agent.agent_id, db, owner)
        assert progress.counts["skipped"] == 1
        assert not progress.hand_in.can_hand_in
    drawn = draw(engine, setup, agent.agent_id, sketch())
    with Session(engine) as db:
        owner = db.get(User, owner_id)
        scene = router.get_agent_scene(agent.agent_id, tasks[0], db, owner)
        assert scene.progress.state == "draft"
        assert scene.task.buildings == drawn
        router.set_agent_scene_outcome(
            agent.agent_id,
            tasks[0],
            AgentSceneOutcome(outcome="complete"),
            db,
            owner,
        )
        assert router.next_agent_tasks(agent.agent_id, db, owner).tasks == []
        assert router.agent_building_progress(agent.agent_id, db, owner).counts["skipped"] == 1
        router.set_agent_scene_outcome(
            agent.agent_id,
            tasks[1],
            AgentSceneOutcome(outcome="empty"),
            db,
            owner,
        )
        result = router.hand_in_agent_buildings(agent.agent_id, db, owner)
        assert result.hand_in.handed_in_at is not None
    with pytest.raises(HTTPException, match="hand"):
        draw(engine, setup, agent.agent_id, sketch())


def test_delete_and_reconcile_with_ownership_checks(engine, setup):
    agent = register(engine, setup, AgentRegister(name="delete", task_count=1))
    first = draw(engine, setup, agent.agent_id, sketch())
    body = AgentBuildingsDelete(building_ids=[first[0].building_id])
    with Session(engine) as db:
        owner = db.get(User, setup[0])
        assert router.delete_agent_buildings(agent.agent_id, setup[2][0], body, db, owner) == []
    later = draw(engine, setup, agent.agent_id, sketch())
    with Session(engine) as db:
        owner = db.get(User, setup[0])
        assert (
            router.get_agent_scene(agent.agent_id, setup[2][0], db, owner).task.buildings == later
        )
        with pytest.raises(HTTPException, match="Not this agent"):
            router.delete_agent_buildings(agent.agent_id, setup[2][0], body, db, owner)
        with pytest.raises(HTTPException, match="not assigned"):
            router.get_agent_scene(agent.agent_id, setup[2][1], db, owner)
        stranger = User(id=uuid4())
        with pytest.raises(HTTPException, match="Agent not found"):
            service.get_agent(db, agent.agent_id, stranger)


def test_unassigned_agent_does_not_inherit_all_scenes(engine, setup):
    agent = register(engine, setup, AgentRegister(name="none", task_count=0))
    with Session(engine) as db:
        result = router.agent_building_progress(agent.agent_id, db, db.get(User, setup[0]))
        assert result.scenes == []
        assert not result.hand_in.can_hand_in


def test_execution_credential_http_boundaries_and_lifecycle(engine, setup, monkeypatch):
    from src.main import app

    agent = register(engine, setup, AgentRegister(name="execution", task_count=1))
    other = register(engine, setup, AgentRegister(name="other", task_count=1))
    with Session(engine) as db:
        capability = credentials.mint(db, agent.agent_id, 3600)

    def database():
        with Session(engine) as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    client = TestClient(app, headers={"Authorization": f"Bearer {capability.token}"})
    aid, cid, tid = agent.agent_id, setup[1], setup[2][0]
    assert client.get(f"/api/agents/{aid}/tasks/{tid}/scene").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/detailed").status_code == 200
    for path in [
        f"/api/agents/{other.agent_id}",
        f"/api/campaigns/{cid + 1000}/detailed",
        f"/api/campaigns/{cid}/buildings/evaluation?user_id={aid}",
        f"/api/campaigns/{cid}/export-annotations",
        f"/api/campaigns/{cid}/export-annotations-geojson?kind=references",
        f"/api/campaigns/{cid}/annotations",
        f"/api/campaigns/{cid}/buildings/references/tiles/19/0/0.pbf",
        "/api/auth/users",
    ]:
        assert client.get(path).status_code == 403, path
    assert client.post(f"/api/agents/{aid}/credentials", json={}).status_code == 403
    assert client.patch(f"/api/agents/{aid}", json={"takes_over_work": True}).status_code == 403
    assert (
        client.post(
            f"/api/agents/{aid}/tasks/{setup[2][1]}/annotate",
            json={"label_id": None, "comment": "unassigned"},
        ).status_code
        == 404
    )
    cookie = client.get("/api/auth/tiler-token")
    assert cookie.status_code == 200
    from src.tilers.tokens import verify

    claims = verify(client.cookies["tiler_token"])
    assert claims["campaigns"] == [str(cid)]
    assert cookie.json()["expires_in"] <= 60
    create = sketch().model_dump(mode="json")
    response = client.post(f"/api/agents/{aid}/tasks/{tid}/buildings", json=create)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 1
    assert (
        client.post(f"/api/agents/{aid}/tasks/{tid}/annotate", json={"label_id": 2}).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/agents/{aid}/tasks/{tid}/outcome",
            json={"outcome": "complete"},
        ).status_code
        == 200
    )
    assert client.post(f"/api/agents/{aid}/buildings/hand-in", json={}).status_code == 200
    with Session(engine) as db:
        credentials.revoke(db, aid, capability.credential_id)
    assert client.get(f"/api/agents/{aid}").status_code == 401
    with Session(engine) as db:
        expired = credentials.mint(db, aid, 60)
        record = db.get(AgentCredential, expired.credential_id)
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    client.headers["Authorization"] = f"Bearer {expired.token}"
    assert client.get(f"/api/agents/{aid}").status_code == 401


def test_edit_keeps_identity_reopens_and_obeys_hand_in(engine, setup):
    agent = register(engine, setup, AgentRegister(name="edit", task_count=1))
    other = register(engine, setup, AgentRegister(name="foreign", task_count=1))
    (building,) = draw(engine, setup, agent.agent_id, sketch())
    body = AgentBuilding(
        geometry_wkt=box(0.003, 0.003, 0.004, 0.004).wkt,
        confidence=5,
    )
    tid = setup[2][0]
    with Session(engine) as db:
        owner = db.get(User, setup[0])
        router.set_agent_scene_outcome(
            agent.agent_id, tid, AgentSceneOutcome(outcome="complete"), db, owner
        )
        result = router.edit_agent_building(
            agent.agent_id, tid, building.building_id, body, db, owner
        )
        assert result[0].building_id == building.building_id
        assert result[0].confidence == 5
        assert router.get_agent_scene(agent.agent_id, tid, db, owner).progress.state == "draft"
        with pytest.raises(HTTPException, match="not owned"):
            router.edit_agent_building(other.agent_id, tid, building.building_id, body, db, owner)
        router.set_agent_scene_outcome(
            agent.agent_id, tid, AgentSceneOutcome(outcome="complete"), db, owner
        )
        router.hand_in_agent_buildings(agent.agent_id, db, owner)
        with pytest.raises(HTTPException, match="hand"):
            router.edit_agent_building(
                agent.agent_id,
                tid,
                building.building_id,
                body,
                db,
                owner,
            )


def test_overlapping_scene_provenance_coverage_and_export_identity(engine, setup):
    from src.annotation import export
    from src.buildings import service as building_service
    from src.buildings.models import BuildingSceneSource
    from src.campaigns.duplication import duplicate_campaign

    agent = register(engine, setup, AgentRegister(name="evaluate", task_count=2))
    (prediction,) = draw(engine, setup, agent.agent_id, sketch())
    with Session(engine) as db:
        owner = db.get(User, setup[0])
        campaign = db.get(Campaign, setup[1])
        # The fixture's two scenes have identical footprints. Their references differ.
        for source_id, task_id, geom in [
            (1, setup[2][0], box(0.001, 0.001, 0.002, 0.002)),
            (2, setup[2][1], box(0.004, 0.004, 0.005, 0.005)),
        ]:
            db.add(
                Annotation(
                    campaign_id=campaign.id,
                    created_by_user_id=owner.id,
                    label_id=1,
                    geometry=AnnotationGeometry(geometry=f"SRID=4326;{geom.wkt}"),
                    source_id=source_id,
                    reference_task_id=task_id,
                )
            )
        db.commit()
        db.add(
            BuildingSceneSource(
                task_id=setup[2][0], source_chip_id="chip-one", raster_metadata={"width": 256}
            )
        )
        db.commit()
        copied = duplicate_campaign(
            db,
            campaign,
            include_tasks=True,
            include_annotations=True,
            include_user_layouts=False,
        )
        copied_tasks = set(
            db.scalars(select(AnnotationTask.id).where(AnnotationTask.campaign_id == copied.id))
        )
        copied_refs = set(
            db.scalars(
                select(Annotation.reference_task_id).where(
                    Annotation.campaign_id == copied.id, Annotation.source_id.is_not(None)
                )
            )
        )
        assert copied_refs == copied_tasks
        assert not copied_tasks.intersection(setup[2])
        copied_source = db.scalar(
            select(BuildingSceneSource).where(BuildingSceneSource.task_id.in_(copied_tasks))
        )
        assert copied_source.source_chip_id == "chip-one"
        router.set_agent_scene_outcome(
            agent.agent_id, setup[2][0], AgentSceneOutcome(outcome="complete"), db, owner
        )
        result = building_service.evaluate(db, campaign, agent.agent_id)
        assert (result.assigned_count, result.finished_count, result.evaluated_count) == (2, 1, 1)
        assert result.coverage["todo"] == 1
        assert result.overall.true_positives == 1
        assert result.overall.reference_count == 1
        assert not result.empty_scene_fpr_has_evidence
        geojson = export.build_annotations_geojson_export(db, campaign, user_id=agent.agent_id)
        props = next(
            f["properties"]
            for f in geojson["features"]
            if f["properties"]["stacnotator_annotation_id"] == prediction.building_id
        )
        assert props["stacnotator_created_by_user_id"] == str(agent.agent_id)
        assert props["stacnotator_created_by_user_email"]
        assert props["stacnotator_origin_task_id"] == setup[2][0]
        assert props["stacnotator_confidence"] == 4
        reference = db.scalar(
            select(Annotation).where(
                Annotation.campaign_id == campaign.id, Annotation.source_id == 2
            )
        )
        reference.reference_task_id = None
        db.commit()
        with pytest.raises(HTTPException, match="provenance missing or ambiguous"):
            building_service.evaluate(db, campaign, agent.agent_id)


def test_no_assigned_or_evaluated_scenes_are_no_evidence(engine, setup):
    from src.buildings import service as building_service

    agent = register(engine, setup, AgentRegister(name="no-evidence", task_count=0))
    with Session(engine) as db:
        result = building_service.evaluate(db, db.get(Campaign, setup[1]), agent.agent_id)
        assert result.status == "no_evidence"
        assert result.assigned_count == result.evaluated_count == 0
        assert not result.empty_scene_fpr_has_evidence

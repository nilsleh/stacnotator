"""Building campaigns: scene rules, outcomes, hand-in and exports (src.buildings)."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from shapely.geometry import box

from src.annotation import export
from src.annotation.schemas import AnnotationCreate, AnnotationFromTaskCreate, AnnotationUpdate
from src.buildings import lifecycle
from src.buildings.config import BuildingScenes, building_scenes
from src.campaigns import service as campaigns_service

USER = uuid4()
CELL = box(0, 0, 0.01, 0.01)
INSIDE = box(0.001, 0.001, 0.002, 0.002)
CFG = {"building_label_id": 1, "complete_label_id": 2, "empty_label_id": 3}


def campaign(cfg=CFG):
    return NS(id=7, settings=NS(building_scenes=cfg))


def create(**kw):
    values = {
        "label_id": 1,
        "comment": None,
        "geometry_wkt": INSIDE.wkt,
        "confidence": 3,
        "origin_task_id": 11,
    }
    return AnnotationCreate(**{**values, **kw})


def submission(label_id, **kw):
    return AnnotationFromTaskCreate(label_id=label_id, comment=None, **kw)


def update(**kw):
    values = {"label_id": None, "comment": None, "geometry_wkt": None, "is_authoritative": None}
    return AnnotationUpdate(**{**values, **kw})


@pytest.fixture(autouse=True)
def not_handed_in(monkeypatch):
    monkeypatch.setattr(lifecycle, "get_hand_in", lambda *a: None)


@pytest.fixture
def cells(monkeypatch):
    monkeypatch.setattr(lifecycle, "_scene_cells", lambda db, cid, ids: {i: CELL for i in ids})


@pytest.fixture
def counts(monkeypatch):
    def set_counts(buildings, unscored=0):
        monkeypatch.setattr(
            lifecycle,
            "_building_counts",
            lambda db, user, ids: {i: (buildings, unscored) for i in ids},
        )

    return set_counts


# --- configuration -----------------------------------------------------------


def test_only_campaigns_with_a_building_config_are_building_campaigns():
    assert building_scenes(campaign()) == BuildingScenes(**CFG)
    assert building_scenes(campaign(None)) is None
    assert building_scenes(NS(settings=MagicMock())) is None


def test_config_labels_must_be_distinct_and_match_the_campaign_labels():
    with pytest.raises(ValueError):
        BuildingScenes(building_label_id=1, complete_label_id=1, empty_label_id=3)
    cfg = BuildingScenes(**CFG)
    cfg.check_labels(
        {
            "1": {"name": "Building", "geometry_type": "polygon"},
            "2": {"name": "a"},
            "3": {"name": "b"},
        }
    )
    with pytest.raises(ValueError, match="polygon"):
        cfg.check_labels({"1": {"name": "Building"}, "2": {"name": "a"}, "3": {"name": "b"}})


# --- building geometry ---------------------------------------------------------


@pytest.mark.parametrize(
    "wkt",
    [
        "POINT (0.001 0.001)",
        "POLYGON EMPTY",
        "POLYGON ((0 0, 0.002 0.002, 0 0.002, 0.002 0, 0 0))",  # bow-tie
        "POLYGON Z ((0 0 1, 0.002 0 1, 0.002 0.002 1, 0 0 1))",
        "MULTIPOLYGON (((0 0, 0.001 0, 0.001 0.001, 0 0)))",
        box(0.009, 0.009, 0.011, 0.011).wkt,  # crosses the outline
        "POLYGON ((0 0, nan 0, 0.001 0.001, 0 0))",
        "invalid",
    ],
)
def test_invalid_buildings_are_rejected(wkt):
    with pytest.raises(HTTPException) as exc:
        lifecycle.validate_building_geometry(wkt, CELL)
    assert exc.value.status_code == 400


def test_a_building_may_touch_the_scene_outline():
    lifecycle.validate_building_geometry(box(0.008, 0.008, 0.01, 0.01).wkt, CELL)


# --- creating and editing buildings --------------------------------------------


def test_other_campaigns_are_untouched():
    db = MagicMock()
    assert lifecycle.check_new_buildings(db, campaign(None), [create(label_id=9)], USER) == set()
    lifecycle.check_annotation_update(db, campaign(None), NS(), update(label_id=9), USER)
    assert lifecycle.reopen_scenes(db, campaign(None), {(USER, 11)}) == []
    db.scalars.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.parametrize(
    ("item", "match"),
    [
        (create(label_id=2), "Only buildings"),
        (create(origin_task_id=None), "scene"),
        (create(confidence=None), "confidence"),
        (create(confidence=0), "confidence"),
        (create(confidence=6), "confidence"),
        (create(geometry_wkt=box(0.009, 0.009, 0.011, 0.011).wkt), "inside its scene"),
    ],
)
def test_new_buildings_need_label_scene_score_and_a_place_in_the_scene(cells, item, match):
    with pytest.raises(HTTPException, match=match):
        lifecycle.check_new_buildings(MagicMock(), campaign(), [item], USER)


def test_one_bad_building_fails_the_whole_batch_before_anything_is_written(cells):
    db = MagicMock()
    with pytest.raises(HTTPException):
        lifecycle.check_new_buildings(
            db, campaign(), [create(), create(geometry_wkt="POINT (0 0)")], USER
        )
    db.add.assert_not_called()
    db.execute.assert_not_called()


def test_valid_buildings_report_the_scenes_they_join(cells):
    assert lifecycle.check_new_buildings(
        MagicMock(), campaign(), [create(), create(origin_task_id=12)], USER
    ) == {11, 12}


def test_building_edits_keep_label_score_and_scene(cells):
    building = NS(
        created_by_user_id=USER,
        annotation_task_id=None,
        origin_task_id=11,
        label_id=1,
        confidence=3,
    )
    for bad in (
        update(label_id=2),
        update(confidence=0),
        update(geometry_wkt=box(0.009, 0.009, 0.011, 0.011).wkt),
    ):
        with pytest.raises(HTTPException):
            lifecycle.check_annotation_update(MagicMock(), campaign(), building, bad, USER)
    lifecycle.check_annotation_update(
        MagicMock(), campaign(), building, update(geometry_wkt=INSIDE.wkt, confidence=5), USER
    )


def test_an_unscored_building_must_be_scored_when_edited(cells):
    legacy = NS(
        created_by_user_id=USER,
        annotation_task_id=None,
        origin_task_id=11,
        label_id=1,
        confidence=None,
    )
    with pytest.raises(HTTPException, match="confidence"):
        lifecycle.check_annotation_update(
            MagicMock(), campaign(), legacy, update(comment="x"), USER
        )


def test_a_scene_outcome_cannot_be_relabelled_through_an_edit():
    outcome = NS(created_by_user_id=USER, annotation_task_id=11, origin_task_id=None, label_id=2)
    with pytest.raises(HTTPException, match="Complete, Empty"):
        lifecycle.check_annotation_update(
            MagicMock(), campaign(), outcome, update(label_id=3), USER
        )


def test_a_hand_in_locks_the_owners_scenes(monkeypatch, cells):
    monkeypatch.setattr(lifecycle, "get_hand_in", lambda *a: object())
    building = NS(
        created_by_user_id=USER,
        annotation_task_id=None,
        origin_task_id=11,
        label_id=1,
        confidence=3,
    )
    for attempt in (
        lambda: lifecycle.check_new_buildings(MagicMock(), campaign(), [create()], USER),
        lambda: lifecycle.check_annotation_update(
            MagicMock(), campaign(), building, update(confidence=5), USER
        ),
        lambda: lifecycle.check_scene_outcome(
            MagicMock(), campaign(), NS(id=11), USER, submission(None)
        ),
        lambda: lifecycle.deletion_effects(MagicMock(), campaign(), [building], USER),
    ):
        with pytest.raises(HTTPException) as exc:
            attempt()
        assert exc.value.status_code == 409


def test_an_admin_deleting_handed_in_buildings_is_not_locked_out(monkeypatch):
    monkeypatch.setattr(lifecycle, "get_hand_in", lambda *a: object())
    building = NS(created_by_user_id=USER, annotation_task_id=None, origin_task_id=11)
    effects = lifecycle.deletion_effects(MagicMock(), campaign(), [building], uuid4())
    assert effects.building_owners == {(USER, 11)}


# --- scene outcomes ------------------------------------------------------------


def test_complete_needs_a_building(counts):
    counts(0)
    with pytest.raises(HTTPException) as exc:
        lifecycle.check_scene_outcome(MagicMock(), campaign(), NS(id=11), USER, submission(2))
    assert exc.value.status_code == 409


def test_complete_needs_every_building_scored(counts):
    counts(2, unscored=1)
    with pytest.raises(HTTPException, match="Score every building"):
        lifecycle.check_scene_outcome(MagicMock(), campaign(), NS(id=11), USER, submission(2))


def test_empty_is_refused_while_buildings_remain(counts):
    counts(1)
    with pytest.raises(HTTPException) as exc:
        lifecycle.check_scene_outcome(MagicMock(), campaign(), NS(id=11), USER, submission(3))
    assert exc.value.status_code == 409


def test_skip_keeps_buildings_and_valid_outcomes_pass(counts):
    counts(2)
    lifecycle.check_scene_outcome(MagicMock(), campaign(), NS(id=11), USER, submission(None))
    lifecycle.check_scene_outcome(MagicMock(), campaign(), NS(id=11), USER, submission(2))
    counts(0)
    lifecycle.check_scene_outcome(MagicMock(), campaign(), NS(id=11), USER, submission(3))


@pytest.mark.parametrize("bad", [submission(1), submission(2, is_authoritative=True)])
def test_buildings_and_authoritative_submissions_are_not_outcomes(counts, bad):
    counts(1)
    with pytest.raises(HTTPException) as exc:
        lifecycle.check_scene_outcome(MagicMock(), campaign(), NS(id=11), USER, bad)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("outcome", "buildings", "state"),
    [
        (2, 1, "complete"),
        (3, 0, "empty"),
        (None, 3, "skipped"),
        (lifecycle.NO_OUTCOME, 2, "draft"),
        (lifecycle.NO_OUTCOME, 0, "todo"),
    ],
)
def test_scene_states(outcome, buildings, state):
    assert lifecycle.scene_state(BuildingScenes(**CFG), outcome, buildings) == state


def test_reopening_clears_only_the_edited_users_outcome_and_their_hand_in(monkeypatch):
    other = uuid4()
    mine = NS(id=1, created_by_user_id=USER, annotation_task_id=11, geometry_id=5)
    theirs = NS(id=2, created_by_user_id=other, annotation_task_id=11, geometry_id=5)
    unrelated = NS(id=3, created_by_user_id=USER, annotation_task_id=12, geometry_id=6)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [mine, theirs, unrelated]
    deleted_rows = []
    monkeypatch.setattr(
        lifecycle, "delete_rows_and_orphan_geometries", lambda db, rows: deleted_rows.extend(rows)
    )
    withdrawn = []
    monkeypatch.setattr(
        lifecycle, "withdraw_hand_ins", lambda db, cid, users: withdrawn.extend(users)
    )

    assert lifecycle.reopen_scenes(db, campaign(), {(USER, 11)}) == [1]
    assert deleted_rows == [mine]
    assert withdrawn == [USER]


def test_deletions_only_count_buildings_and_outcomes():
    building = NS(created_by_user_id=USER, origin_task_id=11, annotation_task_id=None)
    outcome = NS(created_by_user_id=USER, origin_task_id=None, annotation_task_id=11)
    effects = lifecycle.deletion_effects(MagicMock(), campaign(), [building, outcome], uuid4())
    assert effects.building_owners == {(USER, 11)}
    assert effects.outcome_owners == {USER}
    assert (
        lifecycle.deletion_effects(
            MagicMock(), campaign(None), [building, outcome], USER
        ).building_owners
        == set()
    )


# --- hand-in -------------------------------------------------------------------


def _summary(state, n=1):
    return lifecycle.SceneSummary(
        task_id=n, annotation_number=n, state=state, buildings=0, unscored=0
    )


def test_hand_in_is_refused_while_a_scene_is_skipped_draft_or_todo(monkeypatch):
    for blocker in ("skipped", "draft", "todo"):
        monkeypatch.setattr(
            lifecycle,
            "scene_summaries",
            lambda db, c, cfg, u, blocker=blocker: [_summary("complete"), _summary(blocker, 2)],
        )
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            lifecycle.hand_in(db, campaign(), USER)
        assert exc.value.status_code == 409
        db.add.assert_not_called()


def test_hand_in_records_once_when_every_scene_is_complete_or_empty(monkeypatch):
    monkeypatch.setattr(
        lifecycle, "scene_summaries", lambda *a: [_summary("complete"), _summary("empty", 2)]
    )
    monkeypatch.setattr(lifecycle, "get_hand_in", lambda *a: None)
    db = MagicMock()
    lifecycle.hand_in(db, campaign(), USER)
    db.add.assert_called_once()
    monkeypatch.setattr(lifecycle, "get_hand_in", lambda *a: object())
    db = MagicMock()
    lifecycle.hand_in(db, campaign(), USER)
    db.add.assert_not_called()


def test_an_unfinished_scene_withdraws_the_hand_in(monkeypatch):
    monkeypatch.setattr(lifecycle, "get_hand_in", lambda *a: object())
    monkeypatch.setattr(lifecycle, "scene_summaries", lambda *a: [_summary("skipped")])
    withdrawn = []
    monkeypatch.setattr(
        lifecycle, "withdraw_hand_ins", lambda db, cid, users: withdrawn.extend(users)
    )
    lifecycle.revalidate_hand_in(MagicMock(), campaign(), USER)
    assert withdrawn == [USER]


def test_hand_in_needs_a_building_campaign():
    with pytest.raises(HTTPException) as exc:
        lifecycle.hand_in(MagicMock(), campaign(None), USER)
    assert exc.value.status_code == 404


# --- exports -------------------------------------------------------------------


def test_building_exports_default_to_predictions_and_others_to_everything():
    assert export.resolve_export_kind(campaign(), None) == "predictions"
    assert export.resolve_export_kind(campaign(None), None) == "all"
    assert export.resolve_export_kind(campaign(), "references") == "references"


def test_an_empty_building_export_keeps_its_header(monkeypatch):
    monkeypatch.setattr(export, "fetch_annotations_with_context", lambda db, c, kind: ([], {}))
    c = NS(id=7, settings=NS(building_scenes=CFG, labels={}, form_fields=[]))
    df = export.build_annotations_export(MagicMock(), c)
    assert df.empty
    assert {
        "stacnotator_annotation_id",
        "stacnotator_origin_task_id",
        "stacnotator_source_id",
    } <= set(df.columns)


# --- scene deletion --------------------------------------------------------------


@pytest.fixture
def building_campaign_tasks(monkeypatch):
    monkeypatch.setattr(campaigns_service.assignments, "_verify_tasks_in_campaign", lambda *a: None)
    monkeypatch.setattr(campaigns_service, "building_scenes", lambda c: BuildingScenes(**CFG))
    monkeypatch.setattr(
        campaigns_service.annotation_service, "record_annotation_deletions", MagicMock()
    )


def test_scenes_with_buildings_are_not_deleted_without_their_buildings(
    building_campaign_tasks, monkeypatch
):
    monkeypatch.setattr(campaigns_service, "scene_building_count", lambda *a: 2)
    db = MagicMock()
    db.scalars.return_value.all.side_effect = [[MagicMock(id=1, geometry_id=11)]]
    with pytest.raises(HTTPException) as exc:
        campaigns_service.delete_annotation_tasks(db, 1, [1])
    assert exc.value.status_code == 409
    db.delete.assert_not_called()


def test_deleting_scenes_with_annotations_takes_their_buildings(building_campaign_tasks):
    task = MagicMock(id=1, geometry_id=11)
    outcome = MagicMock(id=7, geometry_id=11)
    house = MagicMock(id=8, geometry_id=12)
    db = MagicMock()
    db.scalars.return_value.all.side_effect = [[task], [outcome], [house]]

    campaigns_service.delete_annotation_tasks(db, 1, [1], delete_annotations=True)

    assert [c.args[0] for c in db.delete.call_args_list] == [outcome, house, task]
    campaigns_service.annotation_service.record_annotation_deletions.assert_called_once_with(
        db, 1, [7, 8]
    )

"""Scoring buildings against references (src.buildings.evaluation), DB-free.

Fixtures follow the reference branch's ``test_evaluation_service.py``.
"""

import pytest
from shapely.geometry import box

from src.buildings import evaluation as ev


def scene(task_id, buildings, references, split="val", outcome="complete"):
    return ev.SceneInput(task_id, task_id, split, outcome, buildings, references)


def test_exact_matches_score_perfectly():
    a, b = box(0, 0, 10, 10), box(20, 20, 30, 30)
    pairs, fps, fns = ev.match([(1, a), (2, b)], [(101, a), (102, b)])
    assert [(p.building_id, p.reference_id, p.iou) for p in pairs] == [(1, 101, 1.0), (2, 102, 1.0)]
    assert (fps, fns) == ([], [])


def test_partial_overlap_matches_only_above_the_threshold():
    pairs, fps, fns = ev.match(
        [(1, box(2, 0, 12, 10)), (2, box(57, 50, 67, 60))],
        [(10, box(0, 0, 10, 10)), (20, box(50, 50, 60, 60))],
    )
    assert [(p.building_id, p.reference_id) for p in pairs] == [(1, 10)]
    assert pairs[0].iou == pytest.approx(80 / 120)
    assert (fps, fns) == ([2], [20])


def test_matching_is_one_to_one_and_prefers_the_highest_iou():
    ref = box(0, 0, 10, 10)
    pairs, fps, _ = ev.match([(1, box(1, 0, 11, 10)), (2, box(0, 0, 10, 10))], [(9, ref)])
    assert [(p.building_id, p.reference_id) for p in pairs] == [(2, 9)]
    assert fps == [1]


def test_metrics_for_empty_and_missed_scenes():
    perfect_empty = ev.metrics(0, 0, 0, [], 0.5)
    assert (perfect_empty.precision, perfect_empty.recall, perfect_empty.f1) == (1.0, 1.0, 1.0)
    false_alarm = ev.metrics(0, 2, 0, [], 0.5)
    assert (false_alarm.precision, false_alarm.recall, false_alarm.count_error) == (0.0, 0.0, 2)
    all_missed = ev.metrics(0, 0, 3, [], 0.5)
    assert (all_missed.recall, all_missed.count_error) == (0.0, -3)


def test_size_buckets_use_metric_area():
    small = box(0, 0, 4e-5, 4e-5)  # ~4.5 m square, ~20 m2
    large = box(0, 0, 2e-4, 2e-4)  # ~22 m square, ~500 m2
    assert ev.size_bucket(ev.area_m2(small)) == "small_lt_25m2"
    assert ev.size_bucket(ev.area_m2(large)) == "large_gt_100m2"
    assert ev.size_bucket(50) == "medium_25_to_100m2"


def test_campaign_pools_splits_buckets_and_the_empty_scene_rate():
    house = box(0.001, 0.001, 0.0012, 0.0012)  # ~490 m2
    shed = box(0.005, 0.005, 0.00504, 0.00504)  # ~20 m2
    result = ev.evaluate_scenes(
        [
            scene(1, [(1, house)], [(101, house), (102, shed)], split="val"),
            scene(2, [], [], split="test", outcome="empty"),
            scene(3, [(3, shed)], [], split="test", outcome="complete"),
        ]
    )
    assert (result.overall.true_positives, result.overall.false_positives) == (1, 1)
    assert result.overall.false_negatives == 1
    assert result.by_split["val"].recall == pytest.approx(0.5)
    assert result.by_split["test"].false_positives == 1
    assert result.by_size_bucket["large_gt_100m2"].true_positives == 1
    assert result.by_size_bucket["small_lt_25m2"].false_negatives == 1
    assert result.by_size_bucket["small_lt_25m2"].false_positives == 1
    assert result.by_size_bucket["medium_25_to_100m2"].f1 == 0.0
    assert (result.empty_scenes, result.empty_scenes_with_false_positives) == (2, 1)
    assert result.empty_scene_fpr == pytest.approx(0.5)
    assert [s.task_id for s in result.scenes] == [1, 2, 3]


def test_no_finished_scenes_is_no_evidence():
    result = ev.evaluate_scenes([])
    assert (result.overall.f1, result.empty_scene_fpr, result.scenes) == (0.0, 0.0, [])

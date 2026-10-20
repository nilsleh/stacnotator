"""Scoring annotators' buildings against a building campaign's reference footprints.

Pure: shapely in, metrics out, no database (the loader is ``service.evaluate``). Ported
from the reference branch's ``annotation/evaluation_service.py``: greedy one-to-one IoU
matching, precision / recall / F1 / mean IoU, size buckets and the empty-scene false
positive rate. Its temporal deduplication and pixel-mask scoring are not carried over:
every curated dataset is polygon-referenced and a scene's buildings are drawn once.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from shapely import STRtree
from shapely.geometry.base import BaseGeometry

from src.buildings.schemas import (
    BuildingMatchOut,
    BuildingMetricsOut,
    BuildingsEvaluationOut,
    SceneEvaluationOut,
)

DEFAULT_IOU_THRESHOLD = 0.5
SIZE_BUCKETS = ("small_lt_25m2", "medium_25_to_100m2", "large_gt_100m2")
METERS_PER_DEGREE_LAT = 111_320.0

Shape = tuple[int, BaseGeometry]


@dataclass(frozen=True)
class SceneInput:
    """One finished scene: the annotator's buildings and the references inside it."""

    task_id: int
    annotation_number: int
    split: str | None
    outcome: str
    buildings: list[Shape]
    references: list[Shape]


@dataclass
class _Tally:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    ious: list[float] = field(default_factory=list)

    def add(self, metrics: BuildingMetricsOut, ious: Iterable[float]) -> None:
        self.tp += metrics.true_positives
        self.fp += metrics.false_positives
        self.fn += metrics.false_negatives
        self.ious.extend(ious)


def iou(a: BaseGeometry, b: BaseGeometry) -> float:
    if not a.intersects(b):
        return 0.0
    inter = a.intersection(b).area
    union = a.area + b.area - inter
    return float(inter / union) if inter > 0 and union > 0 else 0.0


def metrics(tp: int, fp: int, fn: int, ious: list[float], threshold: float) -> BuildingMetricsOut:
    """Nothing drawn and nothing to find scores a perfect 1.0: an empty scene marked so."""
    predicted, reference = tp + fp, tp + fn
    if predicted == 0 and reference == 0:
        precision = recall = f1 = 1.0
    else:
        precision = tp / predicted if predicted else 0.0
        recall = tp / reference if reference else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return BuildingMetricsOut(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        mean_iou=sum(ious) / len(ious) if ious else 0.0,
        predicted_count=predicted,
        reference_count=reference,
        count_error=predicted - reference,
        iou_threshold=threshold,
    )


def match(
    buildings: list[Shape], references: list[Shape], threshold: float = DEFAULT_IOU_THRESHOLD
) -> tuple[list[BuildingMatchOut], list[int], list[int]]:
    """Greedy one-to-one matching, best IoU first (ties by id). Pairs below ``threshold``
    never match. Returns the matches and the unmatched building and reference ids."""
    candidates: list[tuple[float, int, int]] = []
    if buildings and references:
        tree = STRtree([geom for _, geom in references])
        for b, (_, geom) in enumerate(buildings):
            for r in tree.query(geom, predicate="intersects"):
                score = iou(geom, references[int(r)][1])
                if score >= threshold:
                    candidates.append((score, b, int(r)))
    candidates.sort(key=lambda c: (-c[0], buildings[c[1]][0], references[c[2]][0]))

    used_b: set[int] = set()
    used_r: set[int] = set()
    pairs = []
    for score, b, r in candidates:
        if b in used_b or r in used_r:
            continue
        used_b.add(b)
        used_r.add(r)
        pairs.append(
            BuildingMatchOut(building_id=buildings[b][0], reference_id=references[r][0], iou=score)
        )
    false_positives = [bid for i, (bid, _) in enumerate(buildings) if i not in used_b]
    false_negatives = [rid for i, (rid, _) in enumerate(references) if i not in used_r]
    return pairs, false_positives, false_negatives


def area_m2(geom: BaseGeometry) -> float:
    """Approximate footprint area of a WGS84 polygon, scaled by latitude."""
    lat = max(-89.9, min(89.9, geom.centroid.y))
    return float(geom.area * METERS_PER_DEGREE_LAT**2 * math.cos(math.radians(lat)))


def size_bucket(area: float) -> str:
    if area < 25:
        return SIZE_BUCKETS[0]
    return SIZE_BUCKETS[1] if area <= 100 else SIZE_BUCKETS[2]


def evaluate_scene(
    scene: SceneInput, threshold: float = DEFAULT_IOU_THRESHOLD
) -> SceneEvaluationOut:
    pairs, fps, fns = match(scene.buildings, scene.references, threshold)
    return SceneEvaluationOut(
        task_id=scene.task_id,
        annotation_number=scene.annotation_number,
        split=scene.split,
        outcome=scene.outcome,
        metrics=metrics(len(pairs), len(fps), len(fns), [p.iou for p in pairs], threshold),
        matches=pairs,
        false_positive_ids=fps,
        false_negative_ids=fns,
    )


def evaluate_scenes(
    scenes: list[SceneInput], threshold: float = DEFAULT_IOU_THRESHOLD
) -> BuildingsEvaluationOut:
    """Score each scene and pool the counts: overall, per split and per size bucket (a
    match by its reference's size, a false positive by the building's, a miss by the
    reference's). A scene with no references counts toward the empty-scene false
    positive rate, failed when anything was drawn in it."""
    overall = _Tally()
    by_split: dict[str, _Tally] = {}
    by_bucket = {bucket: _Tally() for bucket in SIZE_BUCKETS}
    empty = empty_with_fp = 0
    results = []
    for scene in scenes:
        result = evaluate_scene(scene, threshold)
        results.append(result)
        ious = [p.iou for p in result.matches]
        overall.add(result.metrics, ious)
        if scene.split:
            by_split.setdefault(scene.split, _Tally()).add(result.metrics, ious)
        if not scene.references:
            empty += 1
            empty_with_fp += bool(scene.buildings)

        building_size = {bid: size_bucket(area_m2(g)) for bid, g in scene.buildings}
        reference_size = {rid: size_bucket(area_m2(g)) for rid, g in scene.references}
        for pair in result.matches:
            tally = by_bucket[reference_size[pair.reference_id]]
            tally.tp += 1
            tally.ious.append(pair.iou)
        for bid in result.false_positive_ids:
            by_bucket[building_size[bid]].fp += 1
        for rid in result.false_negative_ids:
            by_bucket[reference_size[rid]].fn += 1

    def score(t: _Tally) -> BuildingMetricsOut:
        return metrics(t.tp, t.fp, t.fn, t.ious, threshold)

    # "Nothing to find, nothing drawn" is a perfect scene, but no scenes at all - or a
    # size class nothing fell into - is no evidence, so those report zeros.
    no_data = metrics(0, 0, 0, [], threshold).model_copy(
        update={"precision": 0.0, "recall": 0.0, "f1": 0.0}
    )
    return BuildingsEvaluationOut(
        iou_threshold=threshold,
        overall=score(overall) if results else no_data,
        by_split={split: score(t) for split, t in sorted(by_split.items())},
        by_size_bucket={
            bucket: score(t) if t.tp + t.fp + t.fn else no_data for bucket, t in by_bucket.items()
        },
        empty_scenes=empty,
        empty_scenes_with_false_positives=empty_with_fp,
        empty_scene_fpr=empty_with_fp / empty if empty else 0.0,
        scenes=results,
        status="evaluated" if results else "no_evidence",
        assigned_count=len(scenes),
        finished_count=len(scenes),
        evaluated_count=len(scenes),
        empty_scene_fpr_has_evidence=bool(empty),
    )

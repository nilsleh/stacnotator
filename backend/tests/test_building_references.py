"""A building campaign's references are held out of ordinary reads (src.buildings.references)."""

from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from src.annotation import router as annotation_router
from src.annotation.tiles import HELD_OUT_REFERENCES_SQL, ONLY_REFERENCES_SQL, build_mvt_query
from src.buildings import references

CFG = {"building_label_id": 1, "complete_label_id": 2, "empty_label_id": 3}
BUILDING = NS(id=7, settings=NS(building_scenes=CFG))
OTHER = NS(id=8, settings=NS(building_scenes=None))


def test_annotation_tiles_hold_references_out_and_the_admin_tile_has_only_them():
    sql, _ = build_mvt_query(16, 1, 1, campaign_id=7)
    assert HELD_OUT_REFERENCES_SQL in sql
    sql, _ = build_mvt_query(16, 1, 1, campaign_id=7, references=True)
    assert ONLY_REFERENCES_SQL in sql
    assert HELD_OUT_REFERENCES_SQL not in sql


def test_orm_reads_filter_references_only_in_building_campaigns():
    assert "source_id IS NULL" in str(references.visible_annotations(BUILDING))
    assert str(references.visible_annotations(OTHER)) == "true"


def test_reference_exports_need_a_campaign_admin(monkeypatch):
    admin_checks = []
    monkeypatch.setattr(
        references, "require_campaign_admin", lambda **kw: admin_checks.append(kw["campaign_id"])
    )
    db, user = MagicMock(), NS(id=1)
    for kind in (None, "predictions"):
        annotation_router._require_export_access(db, BUILDING, user, kind)
    assert admin_checks == []
    for kind in ("references", "all"):
        annotation_router._require_export_access(db, BUILDING, user, kind)
    annotation_router._require_export_access(db, OTHER, user, "all")
    assert admin_checks == [7, 7]

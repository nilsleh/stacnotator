import asyncio
import base64
import json

import pytest
import responses
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from stacnotator import agent_mcp
from stacnotator._credentials import Credentials, save
from stacnotator._render_browsers import ChromiumMissingError

BASE = "https://app.example.org"
AGENT = "8f7c1f0e-0000-4000-8000-000000000001"
AGENT_OUT = {"agent_id": AGENT, "name": "scout-a-1a2b3c", "campaign_id": 12, "project_id": 3}
IMAGE_BYTES = b"\xff\xd8\xfffake-jpeg"
DEFAULT_VIEW = {"cells": [{"slice_id": 7}]}
TASK = {"task_id": 5, "annotation_number": 1, "lat": 1.5, "lon": 30.0, "geometry_wkt": "POINT"}
NEXT_TASK = {**TASK, "task_id": 6, "annotation_number": 2}


class FakeBrowsers:
    def __init__(self):
        self.calls = []
        self.shown = []
        self.watching = []
        self.meta = {"cells": [{"index": 0}]}

    async def call(self, agent_id, campaign_id, method, *args):
        self.calls.append((method, *args))
        if method == "defaultViews":
            return [DEFAULT_VIEW]
        if method == "render":
            _, views, _limits = args
            return [
                {"view": views[0], "error": "unknown slice 7"},
                *(
                    {
                        "view": view,
                        "image_base64": base64.b64encode(IMAGE_BYTES).decode(),
                        "meta": self.meta,
                    }
                    for view in views
                ),
            ]
        return None

    async def show(self, agent, task_id, images):
        self.shown.append((agent, task_id, len(images)))

    async def open_watch_window(self, agents_url=None):
        self.watching.append(agents_url)


@pytest.fixture
def browsers(monkeypatch):
    save(Credentials(url=BASE, auth={"mode": "none"}, api_url=BASE))
    fake = FakeBrowsers()
    monkeypatch.setattr(agent_mcp, "_browsers", lambda: fake)
    monkeypatch.setattr(agent_mcp, "_session_url", BASE)
    agent_mcp._signed_in_http.cache_clear()
    agent_mcp._agents.clear()
    agent_mcp._default_views.clear()
    agent_mcp._current_tasks.clear()
    agent_mcp._render_refs.clear()
    agent_mcp._image_limits.clear()
    yield fake
    agent_mcp._signed_in_http.cache_clear()


@responses.activate
def test_next_task_draws_the_task_and_preloads_the_ones_after_it(browsers):
    responses.get(f"{BASE}/api/agents/{AGENT}", json=AGENT_OUT)
    responses.post(
        f"{BASE}/api/agents/{AGENT}/next",
        json={"campaign_id": 12, "remaining": 3, "tasks": [TASK, NEXT_TASK]},
    )

    limits = {"max_edge_px": 1000, "max_megapixels": 1.0}
    asyncio.run(agent_mcp.set_image_limits(AGENT, 1000, 1.0))

    text, image = asyncio.run(agent_mcp.next_task(AGENT))

    summary = json.loads(text)
    assert summary["task"] == TASK
    assert summary["remaining"] == 3
    assert summary["views"][0] == {"view": DEFAULT_VIEW, "error": "unknown slice 7"}
    assert summary["views"][1]["image"] == 1
    assert isinstance(image, Image)
    assert image.data == IMAGE_BYTES
    assert ("render", TASK, [DEFAULT_VIEW], limits) in browsers.calls
    assert ("preload", [NEXT_TASK], [DEFAULT_VIEW], limits) in browsers.calls
    assert browsers.shown == [("scout-a-1a2b3c", 5, 1)]


def test_get_views_only_draws_the_current_task(browsers):
    with pytest.raises(ToolError, match="call next_task"):
        asyncio.run(agent_mcp.get_views(AGENT, 5, [DEFAULT_VIEW]))


@responses.activate
def test_resume_reads_scene_without_releasing_assignments(browsers):
    state = {"task": TASK, "progress": {"state": "skipped"}, "hand_in": {"handed_in_at": None}}
    responses.get(f"{BASE}/api/agents/{AGENT}/tasks/5/scene", json=state)
    assert json.loads(asyncio.run(agent_mcp.open_scene(AGENT, 5))) == state
    assert agent_mcp._current_tasks[AGENT] == TASK
    assert len(responses.calls) == 1


@responses.activate
def test_empty_queue_does_not_claim_completion(browsers):
    responses.post(f"{BASE}/api/agents/{AGENT}/next", json={"tasks": [], "remaining": 0})
    result = json.loads(asyncio.run(agent_mcp.next_task(AGENT))[0])
    assert "skipped scenes are unfinished" in result["note"]


@responses.activate
def test_registration_forwards_caller_operation_id(browsers):
    responses.post(f"{BASE}/api/campaigns/12/agents", json=AGENT_OUT)
    asyncio.run(agent_mcp.register_agent(12, "retry", 1000, 1, operation_id=AGENT))
    assert json.loads(responses.calls[0].request.body)["operation_id"] == AGENT


@responses.activate
def test_api_errors_reach_the_model_as_tool_errors(browsers):
    responses.post(
        f"{BASE}/api/agents/{AGENT}/tasks/9/annotate",
        json={"detail": "required form fields missing: Crop stage"},
        status=400,
    )

    with pytest.raises(ToolError, match="Crop stage"):
        asyncio.run(agent_mcp.submit_label(AGENT, 9, label_id=1, confidence=4, comment="bare soil"))


@responses.activate
def test_register_agent_keeps_the_agent_when_chromium_is_missing(browsers, monkeypatch):
    async def missing(*args):
        raise ChromiumMissingError()

    monkeypatch.setattr(browsers, "call", missing)
    responses.post(f"{BASE}/api/campaigns/12/agents", json=AGENT_OUT)

    result = json.loads(asyncio.run(agent_mcp.register_agent(12, "scout-a", 1000, 1.0)))

    assert result["agent"] == AGENT_OUT
    assert "install_render_browsers" in result["render_browser"]


@responses.activate
def test_registering_opens_the_watch_window_and_names_the_agents_page(browsers):
    responses.post(f"{BASE}/api/campaigns/12/agents", json=AGENT_OUT)

    result = json.loads(asyncio.run(agent_mcp.register_agent(12, "scout-a", 1000, 1.0)))
    assert result["agents_page"] == f"{BASE}/projects/3/campaigns/12/agents"
    assert browsers.watching == [result["agents_page"]]

    asyncio.run(agent_mcp.register_agent(12, "scout-b", 1000, 1.0, watch_window=False))
    assert len(browsers.watching) == 1


@responses.activate
def test_registered_image_limits_reach_every_render(browsers):
    responses.post(f"{BASE}/api/campaigns/12/agents", json=AGENT_OUT)
    responses.post(
        f"{BASE}/api/agents/{AGENT}/next",
        json={"campaign_id": 12, "remaining": 1, "tasks": [TASK]},
    )

    asyncio.run(
        agent_mcp.register_agent(12, "scout-a", max_image_edge_px=2000, max_image_megapixels=3.0)
    )
    asyncio.run(agent_mcp.next_task(AGENT))

    renders = [call for call in browsers.calls if call[0] == "render"]
    assert renders[0][3] == {"max_edge_px": 2000, "max_megapixels": 3.0}


def test_tools_refuse_until_the_user_named_a_deployment(browsers, monkeypatch):
    monkeypatch.setattr(agent_mcp, "_session_url", None)

    with pytest.raises(ToolError, match="full URL"):
        asyncio.run(agent_mcp.list_campaigns())


@pytest.mark.parametrize("url", ["stacnotator.example.org", "app", "ftp://app.example.org"])
def test_login_requires_a_full_url(browsers, url):
    with pytest.raises(ToolError, match="full URL"):
        asyncio.run(agent_mcp.login(url))


@responses.activate
def test_login_reuses_a_saved_login_for_the_same_deployment(browsers, monkeypatch):
    monkeypatch.setattr(agent_mcp, "_session_url", None)
    responses.get(f"{BASE}/api/auth/me", json={"display_name": "rohan"})

    result = json.loads(asyncio.run(agent_mcp.login(BASE + "/")))

    assert result == {"deployment": BASE, "signed_in_as": "rohan"}
    assert agent_mcp._session_url == BASE


SCENE = {
    **TASK,
    "geometry_wkt": "POLYGON((30 1.5,30.001 1.5,30.001 1.501,30 1.501,30 1.5))",
    "buildings": [],
}
MAP_META = {
    "task": {"task_id": 5, "lat": 1.5, "lon": 30.0},
    "cells": [{"i": 0, "kind": "imagery", "zoom": 19, "rect": [0, 0, 512, 512]}],
}


def test_a_traced_pixel_is_its_offset_from_the_cell_centre_in_mercator_pixels():
    from stacnotator import _pixels

    cell = MAP_META["cells"][0]
    assert _pixels.pixel_to_lonlat((30.0, 1.5), cell, 256, 256) == pytest.approx((30.0, 1.5))
    lon, _ = _pixels.pixel_to_lonlat((30.0, 1.5), cell, 512, 256)
    assert lon - 30.0 == pytest.approx(256 / (256 * 2**19) * 360)
    with pytest.raises(ValueError, match="same map cell"):
        _pixels.polygon_wkt(MAP_META, [[10, 10], [600, 10], [10, 20]])


@responses.activate
def test_draw_buildings_sends_the_traced_outline_as_coordinates(browsers):
    responses.get(f"{BASE}/api/agents/{AGENT}", json=AGENT_OUT)
    responses.post(
        f"{BASE}/api/agents/{AGENT}/next",
        json={"campaign_id": 12, "remaining": 1, "tasks": [SCENE]},
    )
    drawn = [{"building_id": 41, "geometry_wkt": "POLYGON((...))", "confidence": 4}]
    responses.post(f"{BASE}/api/agents/{AGENT}/tasks/5/buildings", json=drawn)
    responses.post(f"{BASE}/api/agents/{AGENT}/tasks/5/renders", json={"render_id": AGENT})
    browsers.meta = MAP_META
    asyncio.run(agent_mcp.set_image_limits(AGENT, 1000, 1.0))
    asyncio.run(agent_mcp.next_task(AGENT))

    with pytest.raises(ToolError, match="render_id"):
        asyncio.run(agent_mcp.draw_buildings(AGENT, 5, 3, []))
    result = json.loads(
        asyncio.run(
            agent_mcp.draw_buildings(
                AGENT,
                5,
                1,
                [{"pixels": [[256, 256], [266, 256], [266, 266]], "confidence": 4}],
                render_id=AGENT,
            )
        )
    )

    body = json.loads(responses.calls[-1].request.body)
    (building,) = body["buildings"]
    assert building["confidence"] == 4
    assert building["geometry_wkt"].startswith("POLYGON((30.000000000 1.500000000,")
    assert result["buildings"] == [{"building_id": 41, "confidence": 4}]
    # The current task now carries the building, so the next views draw it.
    assert agent_mcp._current_tasks[AGENT]["buildings"] == drawn


@responses.activate
def test_render_reference_is_immutable_and_not_the_latest_image(browsers):
    import hashlib

    responses.get(f"{BASE}/api/agents/{AGENT}", json=AGENT_OUT)
    responses.post(f"{BASE}/api/agents/{AGENT}/tasks/5/renders", json={"render_id": "first"})
    rendered = [
        {
            "view": DEFAULT_VIEW,
            "image_base64": base64.b64encode(IMAGE_BYTES).decode(),
            "meta": MAP_META,
        }
    ]
    text, image = asyncio.run(agent_mcp._bundle(AGENT, {"task": SCENE}, rendered))
    entry = json.loads(text)["views"][0]
    assert entry["image_sha256"] == hashlib.sha256(image.data).hexdigest()
    assert entry["render_id"] == "first"
    snapshot = agent_mcp._render_refs["first"]["meta"]
    rendered[0]["meta"] = {**MAP_META, "cells": []}
    assert snapshot["cells"]
    agent_mcp._current_tasks[AGENT] = SCENE
    responses.post(f"{BASE}/api/agents/{AGENT}/tasks/5/buildings", json=[])
    asyncio.run(
        agent_mcp.draw_buildings(
            AGENT,
            5,
            1,
            [{"pixels": [[256, 256], [266, 256], [266, 266]], "confidence": 4}],
            render_id="first",
        )
    )
    assert json.loads(responses.calls[-1].request.body)["render_id"] == "first"
    agent_mcp._render_refs.clear()
    with pytest.raises(ToolError, match="restart"):
        asyncio.run(agent_mcp.draw_buildings(AGENT, 5, 1, [], render_id="first"))


def test_explicit_render_transform_round_trip_with_fractional_zoom_and_caption():
    import math

    from stacnotator import _pixels

    resolution = 0.37
    cell = {
        "rect": [512, 28, 512, 484],
        "zoom": 18.3,
        "projection": "EPSG:3857",
        "pixel_to_map": [resolution, 0, 3_000_000, 0, -resolution, 500_000],
    }
    for x, y in [(512, 28), (768.25, 270.5), (1024, 512)]:
        lon, lat = _pixels.pixel_to_lonlat((0, 0), cell, x, y)
        mx = math.radians(lon) * 6378137
        my = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * 6378137
        assert abs((mx - 3_000_000) / resolution - x) < 1
        assert abs((500_000 - my) / resolution - y) < 1


@responses.activate
def test_execution_mode_never_uses_saved_owner_credentials(browsers, monkeypatch):
    from stacnotator.errors import AuthenticationError

    monkeypatch.setenv("STACNOTATOR_EXECUTION_TOKEN", "stac-agent-test-capability")
    monkeypatch.setenv("STACNOTATOR_API_URL", BASE)
    responses.get(f"{BASE}/api/agents/{AGENT}", status=401, json={"detail": "expired"})
    http = agent_mcp._http()
    assert http.token() == "stac-agent-test-capability"
    with pytest.raises(AuthenticationError, match="expired or revoked"):
        http.get(f"/agents/{AGENT}")
    assert len(responses.calls) == 1
    with pytest.raises(ToolError, match="cannot sign in"):
        asyncio.run(agent_mcp.login(BASE))


@responses.activate
def test_edit_preserves_building_identity_and_converts_holes(browsers):
    agent_mcp._current_tasks[AGENT] = {**SCENE, "buildings": []}
    agent_mcp._render_refs["edit"] = {
        "agent_id": AGENT,
        "task_id": 5,
        "image": 1,
        "meta": MAP_META,
    }
    responses.patch(
        f"{BASE}/api/agents/{AGENT}/tasks/5/buildings/41",
        json=[{"building_id": 41, "confidence": 5}],
    )
    result = json.loads(
        asyncio.run(
            agent_mcp.edit_building(
                AGENT,
                5,
                41,
                1,
                "edit",
                [[256, 256], [286, 256], [286, 286], [256, 286]],
                5,
                operation_id=AGENT,
                holes=[[[261, 261], [266, 261], [266, 266]]],
            )
        )
    )
    body = json.loads(responses.calls[0].request.body)
    assert body["geometry_wkt"].count("),(") == 1
    assert body["render_id"] == "edit"
    assert body["operation_id"] == AGENT
    assert result["buildings"] == [{"building_id": 41, "confidence": 5}]
    assert agent_mcp._current_tasks[AGENT]["buildings"] == result["buildings"]


@pytest.mark.parametrize("field,value", [("agent_id", "another"), ("task_id", 6), ("image", 2)])
def test_pixel_writes_reject_mismatched_render_bindings(browsers, field, value):
    agent_mcp._current_tasks[AGENT] = SCENE
    agent_mcp._render_refs["wrong"] = {
        "agent_id": AGENT,
        "task_id": 5,
        "image": 1,
        "meta": MAP_META,
        field: value,
    }
    with pytest.raises(ToolError, match="mismatched render_id"):
        asyncio.run(
            agent_mcp.edit_building(
                AGENT,
                5,
                41,
                1,
                "wrong",
                [[256, 256], [266, 256], [266, 266]],
                4,
            )
        )


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_failed_imagery_cannot_be_traced_or_finished(browsers, status):
    meta = {
        **MAP_META,
        "status": status,
        "cells": [{**MAP_META["cells"][0], "status": status}],
    }
    agent_mcp._current_tasks[AGENT] = SCENE
    agent_mcp._render_refs["failed"] = {
        "agent_id": AGENT,
        "task_id": 5,
        "image": 1,
        "meta": meta,
    }
    with pytest.raises(ToolError, match="failed or partial"):
        asyncio.run(
            agent_mcp.edit_building(
                AGENT,
                5,
                41,
                1,
                "failed",
                [[256, 256], [266, 256], [266, 266]],
                4,
            )
        )
    with pytest.raises(ToolError, match="successfully rendered"):
        asyncio.run(agent_mcp.finish_scene(AGENT, 5, "empty", "", render_id="failed"))
    assert agent_mcp._current_tasks[AGENT] == SCENE


@responses.activate
def test_finish_scene_forwards_successful_render_reference(browsers):
    agent_mcp._current_tasks[AGENT] = SCENE
    agent_mcp._render_refs["ready"] = {
        "agent_id": AGENT,
        "task_id": 5,
        "image": 1,
        "meta": {"status": "ready", "cells": [{"kind": "imagery", "status": "ready"}]},
    }
    responses.post(f"{BASE}/api/agents/{AGENT}/tasks/5/outcome", json={"task_id": 5})
    asyncio.run(
        agent_mcp.finish_scene(
            AGENT,
            5,
            "empty",
            "No buildings",
            operation_id=AGENT,
            render_id="ready",
        )
    )
    assert json.loads(responses.calls[0].request.body) == {
        "outcome": "empty",
        "comment": "No buildings",
        "operation_id": AGENT,
        "render_id": "ready",
    }
    assert AGENT not in agent_mcp._current_tasks

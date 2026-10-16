"""Unit test for register_cog_on_tiler - no network, mirrors test_tilers_providers style."""

from types import SimpleNamespace

import pytest

from src import net_guard
from src.config import TilerCfg
from src.tilers import providers

TILER = TilerCfg(url="https://tiler.test", allows_ingest=True)


def _fake_post(monkeypatch, payload):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    monkeypatch.setattr(
        providers.httpx,
        "post",
        lambda url, json, headers, timeout: (
            captured.update(url=url, json=json, headers=headers) or _Resp()
        ),
    )
    return captured


def test_register_cog_posts_and_returns_id(monkeypatch):
    monkeypatch.setattr(providers, "mint_tiler_token", lambda *a, **k: "tok")
    monkeypatch.setattr(providers, "assert_public_url", lambda url: None)
    monkeypatch.setattr(providers, "_register_base", lambda tiler: "https://tiler.test")
    captured = _fake_post(monkeypatch, {"id": "search-123"})

    result = providers.register_cog_on_tiler(TILER, "https://x/y.tif", "42")

    assert result == "search-123"
    assert captured["url"] == "https://tiler.test/searches/register-cog"
    assert captured["json"]["cog_url"] == "https://x/y.tif"
    assert captured["json"]["campaign_id"] == "42"
    assert captured["json"]["internal_storage"] is False
    assert captured["headers"]["Authorization"] == "Bearer tok"


def test_register_cog_forwards_internal_storage(monkeypatch):
    monkeypatch.setattr(providers, "mint_tiler_token", lambda *a, **k: "tok")
    monkeypatch.setattr(providers, "assert_public_url", lambda url: None)
    monkeypatch.setattr(providers, "_register_base", lambda tiler: "https://tiler.test")
    captured = _fake_post(monkeypatch, {"id": "s"})

    providers.register_cog_on_tiler(TILER, "https://x/y.tif", "42", internal_storage=True)

    assert captured["json"]["internal_storage"] is True


def test_an_internal_cog_url_is_never_forwarded(monkeypatch):
    """The tiler would fetch it for us, so the guard has to run before the handoff."""
    monkeypatch.setattr(providers, "mint_tiler_token", lambda *a, **k: "tok")

    def _no_post(*args, **kwargs):
        raise AssertionError("must not reach the tiler")

    monkeypatch.setattr(providers.httpx, "post", _no_post)

    with pytest.raises(net_guard.UnsafeUrlError):
        providers.register_cog_on_tiler(TILER, "http://169.254.169.254/latest/meta-data/", 42)


def test_private_cog_forwards_sealed_access_and_requires_tiler_acknowledgement(monkeypatch):
    monkeypatch.setattr(providers, "mint_tiler_token", lambda *a, **k: "tok")
    monkeypatch.setattr(providers, "assert_public_url", lambda url: None)
    sealed = {"kind": "azure_sas", "host": "acct.blob.core.windows.net", "secret": "ciphertext"}
    captured = _fake_post(monkeypatch, {"id": "s", "storage_access": True})
    assert (
        providers.register_cog_on_tiler(
            TILER, "https://acct.blob.core.windows.net/m.tif", "42", sealed_storage_access=sealed
        )
        == "s"
    )
    assert captured["json"]["storage_access"] == sealed
    _fake_post(monkeypatch, {"id": "s"})
    with pytest.raises(ValueError, match="does not support private"):
        providers.register_cog_on_tiler(
            TILER, "https://acct.blob.core.windows.net/m.tif", "42", sealed_storage_access=sealed
        )


def _local_cog_dir(monkeypatch, value):
    monkeypatch.setattr(
        providers, "get_settings", lambda: SimpleNamespace(TILER_LOCAL_COG_DIR=value)
    )


def test_a_local_cog_path_is_forwarded_when_the_tiler_serves_that_dir(monkeypatch):
    _local_cog_dir(monkeypatch, "/data/cogs")
    monkeypatch.setattr(providers, "mint_tiler_token", lambda *a, **k: "tok")
    monkeypatch.setattr(providers, "_register_base", lambda tiler: "https://tiler.test")
    captured = _fake_post(monkeypatch, {"id": "s"})

    providers.register_cog_on_tiler(TILER, "/data/cogs/sn7/2018_01.tif", "42")

    assert captured["json"]["cog_url"] == "/data/cogs/sn7/2018_01.tif"


@pytest.mark.parametrize(
    ("local_dir", "cog_url"),
    [
        (None, "/data/cogs/a.tif"),
        ("/data/cogs", "/data/cogs/../../etc/passwd"),
        ("/data/cogs", "/data/cogsevil/a.tif"),
        ("/data/cogs", "/etc/passwd"),
        ("/data/cogs", "data/cogs/a.tif"),
        ("/data/cogs", "file:///data/cogs/a.tif"),
    ],
)
def test_other_local_paths_are_never_forwarded(monkeypatch, local_dir, cog_url):
    _local_cog_dir(monkeypatch, local_dir)
    monkeypatch.setattr(providers, "mint_tiler_token", lambda *a, **k: "tok")

    def _no_post(*args, **kwargs):
        raise AssertionError("must not reach the tiler")

    monkeypatch.setattr(providers.httpx, "post", _no_post)

    with pytest.raises(net_guard.UnsafeUrlError):
        providers.register_cog_on_tiler(TILER, cog_url, "42")

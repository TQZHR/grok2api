import asyncio
from types import SimpleNamespace

from app.api.v1 import admin as admin_module


class _DummyPool:
    def __init__(self, tokens):
        self._tokens = [SimpleNamespace(token=t) for t in tokens]

    def list(self):
        return list(self._tokens)


class _DummyManager:
    def __init__(self, pools):
        self.pools = pools


def test_nsfw_refresh_api_all_mode_uses_all_tokens(monkeypatch):
    captured = {}
    mgr = _DummyManager(
        pools={
            "ssoBasic": _DummyPool(["sso=token-a", "token-b"]),
            "ssoSuper": _DummyPool(["token-c"]),
        }
    )

    async def _fake_get_token_manager():
        return mgr

    async def _fake_refresh(tokens, concurrency=None, retries=None):
        captured["tokens"] = tokens
        captured["concurrency"] = concurrency
        captured["retries"] = retries
        return {
            "summary": {"total": len(tokens), "success": len(tokens), "failed": 0, "invalidated": 0},
            "failed": [],
        }

    monkeypatch.setattr(admin_module, "get_token_manager", _fake_get_token_manager)
    monkeypatch.setattr(admin_module, "refresh_account_settings_for_tokens", _fake_refresh)

    result = asyncio.run(
        admin_module.refresh_tokens_nsfw_api({"all": True, "concurrency": 5, "retries": 1})
    )

    assert result["status"] == "success"
    assert result["summary"] == {"total": 3, "success": 3, "failed": 0, "invalidated": 0}
    assert result["failed"] == []
    assert captured["tokens"] == ["token-a", "token-b", "token-c"]
    assert captured["concurrency"] == 5
    assert captured["retries"] == 1


def test_nsfw_refresh_api_token_list_mode_normalizes_tokens(monkeypatch):
    captured = {}
    mgr = _DummyManager(pools={})

    async def _fake_get_token_manager():
        return mgr

    async def _fake_refresh(tokens, concurrency=None, retries=None):
        captured["tokens"] = tokens
        return {
            "summary": {"total": len(tokens), "success": 1, "failed": 1, "invalidated": 1},
            "failed": [{"token": "token-b", "step": "nsfw", "error": "forbidden", "attempts": 4}],
        }

    monkeypatch.setattr(admin_module, "get_token_manager", _fake_get_token_manager)
    monkeypatch.setattr(admin_module, "refresh_account_settings_for_tokens", _fake_refresh)

    payload = {"tokens": ["sso=token-a", "sso=token-a;sso-rw=token-rw", "token-b"]}
    result = asyncio.run(admin_module.refresh_tokens_nsfw_api(payload))

    assert result["status"] == "success"
    assert result["summary"]["total"] == 2
    assert result["summary"]["failed"] == 1
    assert result["summary"]["invalidated"] == 1
    assert captured["tokens"] == ["token-a", "token-b"]

import asyncio
from contextlib import asynccontextmanager

from app.api.v1 import admin as admin_module


class _DummyStorage:
    def __init__(self, token_data):
        self._token_data = token_data

    async def load_tokens(self):
        return self._token_data

    async def save_tokens(self, data):
        self._token_data = data

    @asynccontextmanager
    async def acquire_lock(self, _name: str, timeout: int = 10):
        yield


class _DummyTokenManager:
    def __init__(self):
        self.reload_calls = 0

    async def reload(self):
        self.reload_calls += 1


def test_update_tokens_api_triggers_background_for_new_tokens(monkeypatch):
    storage = _DummyStorage({"ssoBasic": [{"token": "token-a", "status": "active", "quota": 80}]})
    mgr = _DummyTokenManager()
    captured = {}

    async def _fake_get_mgr():
        return mgr

    def _fake_trigger(tokens, concurrency, retries):
        captured["tokens"] = tokens
        captured["concurrency"] = concurrency
        captured["retries"] = retries

    monkeypatch.setattr(admin_module, "get_storage", lambda: storage)
    monkeypatch.setattr("app.services.token.manager.get_token_manager", _fake_get_mgr)
    monkeypatch.setattr(admin_module, "_trigger_account_settings_refresh_background", _fake_trigger)
    monkeypatch.setattr(admin_module, "_resolve_nsfw_refresh_concurrency", lambda override=None: 10)
    monkeypatch.setattr(admin_module, "_resolve_nsfw_refresh_retries", lambda override=None: 3)

    payload = {
        "ssoBasic": [
            {"token": "token-a", "status": "active", "quota": 80},
            {"token": "token-b", "status": "active", "quota": 80},
        ]
    }
    result = asyncio.run(admin_module.update_tokens_api(payload))

    assert result["status"] == "success"
    assert result["nsfw_refresh"]["mode"] == "background"
    assert result["nsfw_refresh"]["triggered"] == 1
    assert captured["tokens"] == ["token-b"]
    assert captured["concurrency"] == 10
    assert captured["retries"] == 3
    assert mgr.reload_calls == 1


def test_update_tokens_api_does_not_trigger_when_no_new_tokens(monkeypatch):
    storage = _DummyStorage({"ssoBasic": [{"token": "token-a", "status": "active", "quota": 80}]})
    mgr = _DummyTokenManager()
    captured = {}

    async def _fake_get_mgr():
        return mgr

    def _fake_trigger(tokens, concurrency, retries):
        captured["tokens"] = tokens
        captured["concurrency"] = concurrency
        captured["retries"] = retries

    monkeypatch.setattr(admin_module, "get_storage", lambda: storage)
    monkeypatch.setattr("app.services.token.manager.get_token_manager", _fake_get_mgr)
    monkeypatch.setattr(admin_module, "_trigger_account_settings_refresh_background", _fake_trigger)
    monkeypatch.setattr(admin_module, "_resolve_nsfw_refresh_concurrency", lambda override=None: 10)
    monkeypatch.setattr(admin_module, "_resolve_nsfw_refresh_retries", lambda override=None: 3)

    payload = {"ssoBasic": [{"token": "token-a", "status": "active", "quota": 70, "note": "edited"}]}
    result = asyncio.run(admin_module.update_tokens_api(payload))

    assert result["status"] == "success"
    assert result["nsfw_refresh"]["triggered"] == 0
    assert captured["tokens"] == []
    assert captured["concurrency"] == 10
    assert captured["retries"] == 3
    assert mgr.reload_calls == 1

import asyncio
from types import SimpleNamespace

from app.api.v1 import admin as admin_module


class _DummyStorage:
    def __init__(self, payload):
        self._payload = payload

    async def load_tokens(self):
        return self._payload


def test_tokens_list_default_pagination(monkeypatch):
    payload = {
        "ssoBasic": [
            {"token": f"token-{i}", "status": "active", "quota": 100, "quota_known": True, "note": ""}
            for i in range(40)
        ],
        "ssoSuper": [],
    }

    monkeypatch.setattr(admin_module, "get_storage", lambda: _DummyStorage(payload))

    result = asyncio.run(admin_module.get_tokens_api())

    assert result["total"] == 40
    assert result["page"] == 1
    assert result["per_page"] == 30
    assert result["pages"] == 2
    assert len(result["items"]) == 30


def test_tokens_list_filter_and_page(monkeypatch):
    payload = {
        "ssoBasic": [
            {"token": "alpha", "status": "active", "quota": 10, "quota_known": True, "note": "nsfw enabled"},
            {"token": "beta", "status": "expired", "quota": 0, "quota_known": True, "note": ""},
        ],
        "ssoSuper": [
            {"token": "gamma", "status": "active", "quota": 5, "quota_known": True, "heavy_quota": 5, "heavy_quota_known": True, "note": ""},
        ],
    }

    monkeypatch.setattr(admin_module, "get_storage", lambda: _DummyStorage(payload))

    result = asyncio.run(
        admin_module.get_tokens_api(
            page=1,
            per_page="50",
            token_type="sso",
            status="active",
            nsfw="true",
            search="alp",
        )
    )

    assert result["total"] == 1
    assert result["pages"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["token"] == "alpha"


def test_tokens_list_all_mode(monkeypatch):
    payload = {
        "ssoBasic": [
            {"token": f"item-{i}", "status": "active", "quota": 100, "quota_known": True, "note": ""}
            for i in range(120)
        ],
        "ssoSuper": [],
    }

    monkeypatch.setattr(admin_module, "get_storage", lambda: _DummyStorage(payload))

    result = asyncio.run(admin_module.get_tokens_api(page=1, per_page="all"))

    assert result["total"] == 120
    assert result["per_page"] == "all"
    assert result["pages"] == 1
    assert len(result["items"]) == 120

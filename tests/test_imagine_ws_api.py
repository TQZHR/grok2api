import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.v1 import admin as admin_api


def _build_client(monkeypatch: pytest.MonkeyPatch, api_key: str = "test-key") -> TestClient:
    async def _fake_legacy_keys():
        return set()

    monkeypatch.setattr(admin_api, "_load_legacy_api_keys", _fake_legacy_keys)
    monkeypatch.setattr(
        admin_api,
        "get_config",
        lambda key, default=None: api_key if key == "app.api_key" else default,
    )

    app = FastAPI()
    app.include_router(admin_api.router)
    return TestClient(app)


def _recv_until(ws, predicate, max_messages: int = 80):
    for _ in range(max_messages):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    pytest.fail("Did not receive expected websocket message in time")


def test_imagine_ws_rejects_invalid_api_key(monkeypatch: pytest.MonkeyPatch):
    client = _build_client(monkeypatch, api_key="valid-key")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/v1/admin/imagine/ws?api_key=wrong-key"):
            pass
    assert exc.value.code == 1008


def test_imagine_ws_ping_pong(monkeypatch: pytest.MonkeyPatch):
    client = _build_client(monkeypatch, api_key="valid-key")
    with client.websocket_connect("/api/v1/admin/imagine/ws?api_key=valid-key") as ws:
        ws.send_json({"type": "ping"})
        msg = ws.receive_json()
    assert msg == {"type": "pong"}


def test_imagine_ws_accepts_managed_api_key(monkeypatch: pytest.MonkeyPatch):
    client = _build_client(monkeypatch, api_key="global-key")

    async def _fake_init():
        return None

    monkeypatch.setattr(admin_api.api_key_manager, "init", _fake_init)
    monkeypatch.setattr(
        admin_api.api_key_manager,
        "validate_key",
        lambda token: {"key": token, "is_active": True} if token == "managed-key" else None,
    )

    with client.websocket_connect("/api/v1/admin/imagine/ws?api_key=managed-key") as ws:
        ws.send_json({"type": "ping"})
        msg = ws.receive_json()
    assert msg == {"type": "pong"}


def test_imagine_ws_empty_prompt_error(monkeypatch: pytest.MonkeyPatch):
    client = _build_client(monkeypatch, api_key="valid-key")
    with client.websocket_connect("/api/v1/admin/imagine/ws?api_key=valid-key") as ws:
        ws.send_json({"type": "start", "prompt": "   "})
        msg = ws.receive_json()

    assert msg.get("type") == "error"
    assert msg.get("code") == "empty_prompt"


def test_imagine_ws_start_stop_message_flow(monkeypatch: pytest.MonkeyPatch):
    client = _build_client(monkeypatch, api_key="valid-key")

    class _DummyTokenManager:
        def __init__(self):
            self.sync_calls = 0

        async def reload_if_stale(self):
            return None

        def get_token_for_model(self, _model_id: str):
            return "token-demo"

        async def sync_usage(self, *_args, **_kwargs):
            self.sync_calls += 1
            return True

    token_mgr = _DummyTokenManager()

    async def _fake_get_token_manager():
        return token_mgr

    async def _fake_collect_imagine_batch(_token: str, _prompt: str, _aspect_ratio: str):
        await asyncio.sleep(0.01)
        return ["ZmFrZV9pbWFnZQ=="]

    monkeypatch.setattr(admin_api, "get_token_manager", _fake_get_token_manager)
    monkeypatch.setattr(
        admin_api.ModelService,
        "get",
        lambda model_id: SimpleNamespace(model_id=model_id, is_image=True),
    )
    monkeypatch.setattr(admin_api, "_collect_imagine_batch", _fake_collect_imagine_batch)

    with client.websocket_connect("/api/v1/admin/imagine/ws?api_key=valid-key") as ws:
        ws.send_json({"type": "start", "prompt": "a cat", "aspect_ratio": "1:1"})
        running = _recv_until(
            ws,
            lambda m: m.get("type") == "status" and m.get("status") == "running",
        )
        image = _recv_until(ws, lambda m: m.get("type") == "image")

        ws.send_json({"type": "ping"})
        pong = _recv_until(ws, lambda m: m.get("type") == "pong")

        ws.send_json({"type": "stop"})
        stopped = _recv_until(
            ws,
            lambda m: m.get("type") == "status" and m.get("status") == "stopped",
        )

    assert running.get("aspect_ratio") == "1:1"
    assert isinstance(running.get("run_id"), str) and running.get("run_id")
    assert image.get("b64_json") == "ZmFrZV9pbWFnZQ=="
    assert image.get("aspect_ratio") == "1:1"
    assert int(image.get("sequence") or 0) >= 1
    assert pong == {"type": "pong"}
    assert stopped.get("run_id") == running.get("run_id")
    assert token_mgr.sync_calls >= 1


def test_imagine_ws_stop_immediately_remains_healthy(monkeypatch: pytest.MonkeyPatch):
    client = _build_client(monkeypatch, api_key="valid-key")

    class _DummyTokenManager:
        async def reload_if_stale(self):
            return None

        def get_token_for_model(self, _model_id: str):
            return "token-demo"

        async def sync_usage(self, *_args, **_kwargs):
            return True

    token_mgr = _DummyTokenManager()

    async def _fake_get_token_manager():
        return token_mgr

    async def _slow_collect_imagine_batch(_token: str, _prompt: str, _aspect_ratio: str):
        await asyncio.sleep(0.5)
        return ["ZmFrZV9pbWFnZQ=="]

    monkeypatch.setattr(admin_api, "get_token_manager", _fake_get_token_manager)
    monkeypatch.setattr(
        admin_api.ModelService,
        "get",
        lambda model_id: SimpleNamespace(model_id=model_id, is_image=True),
    )
    monkeypatch.setattr(admin_api, "_collect_imagine_batch", _slow_collect_imagine_batch)

    with client.websocket_connect("/api/v1/admin/imagine/ws?api_key=valid-key") as ws:
        ws.send_json({"type": "start", "prompt": "a fox", "aspect_ratio": "1:1"})
        running = _recv_until(
            ws,
            lambda m: m.get("type") == "status" and m.get("status") == "running",
        )

        ws.send_json({"type": "stop"})
        stopped = _recv_until(
            ws,
            lambda m: m.get("type") == "status" and m.get("status") == "stopped",
            max_messages=120,
        )

        ws.send_json({"type": "ping"})
        pong = _recv_until(ws, lambda m: m.get("type") == "pong")

    assert running.get("run_id")
    assert stopped.get("run_id") == running.get("run_id")
    assert pong == {"type": "pong"}

from fastapi import APIRouter, Depends, HTTPException, Request, Query, Body, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any, Optional

from app.core.auth import verify_api_key
from app.core.config import config, get_config
from app.core.storage import get_storage, LocalStorage, RedisStorage, SQLStorage
import os
from pathlib import Path
import aiofiles
import asyncio
import json
import time
import uuid
import orjson
from starlette.websockets import WebSocketDisconnect, WebSocketState
from app.core.logger import logger
from app.services.register import get_auto_register_manager
from app.services.register.account_settings_refresh import (
    refresh_account_settings_for_tokens,
    normalize_sso_token as normalize_refresh_token,
)
from app.services.api_keys import api_key_manager
from app.services.grok.model import ModelService
from app.services.grok.imagine_generation import (
    collect_experimental_generation_images,
    is_valid_image_value as is_valid_imagine_image_value,
    resolve_aspect_ratio as resolve_imagine_aspect_ratio,
)
from app.services.token import get_token_manager
from app.core.auth import _load_legacy_api_keys


router = APIRouter()

TEMPLATE_DIR = Path(__file__).parent.parent.parent / "static"


class AdminLoginBody(BaseModel):
    username: str | None = None
    password: str | None = None

async def render_template(filename: str):
    """渲染指定模板"""
    template_path = TEMPLATE_DIR / filename
    if not template_path.exists():
        return HTMLResponse(f"Template {filename} not found.", status_code=404)
    
    async with aiofiles.open(template_path, "r", encoding="utf-8") as f:
        content = await f.read()
    return HTMLResponse(content)

@router.get("/", include_in_schema=False)
async def root_redirect():
    """Default entry -> /login (consistent with Workers/Pages)."""
    return RedirectResponse(url="/login", status_code=302)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page():
    """Login page (default)."""
    return await render_template("login/login.html")


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_login_page():
    """Legacy login entry (redirect to /login)."""
    return RedirectResponse(url="/login", status_code=302)

@router.get("/admin/config", response_class=HTMLResponse, include_in_schema=False)
async def admin_config_page():
    """配置管理页"""
    return await render_template("config/config.html")

@router.get("/admin/token", response_class=HTMLResponse, include_in_schema=False)
async def admin_token_page():
    """Token 管理页"""
    return await render_template("token/token.html")

@router.get("/admin/datacenter", response_class=HTMLResponse, include_in_schema=False)
async def admin_datacenter_page():
    """数据中心页"""
    return await render_template("datacenter/datacenter.html")

@router.get("/admin/keys", response_class=HTMLResponse, include_in_schema=False)
async def admin_keys_page():
    """API Key 管理页"""
    return await render_template("keys/keys.html")

@router.get("/chat", response_class=HTMLResponse, include_in_schema=False)
async def chat_page():
    """在线聊天页（公开入口）"""
    return await render_template("chat/chat.html")

@router.get("/admin/chat", response_class=HTMLResponse, include_in_schema=False)
async def admin_chat_page():
    """在线聊天页（后台入口）"""
    return await render_template("chat/chat_admin.html")


async def _verify_ws_api_key(websocket: WebSocket) -> bool:
    api_key = str(get_config("app.api_key", "") or "").strip()
    legacy_keys = await _load_legacy_api_keys()
    if not api_key and not legacy_keys:
        return True
    token = str(websocket.query_params.get("api_key") or "").strip()
    if not token:
        return False
    if (api_key and token == api_key) or token in legacy_keys:
        return True
    try:
        await api_key_manager.init()
        if api_key_manager.validate_key(token):
            return True
    except Exception as e:
        logger.warning(f"Imagine ws api_key validation fallback failed: {e}")
    return False


async def _collect_imagine_batch(token: str, prompt: str, aspect_ratio: str) -> list[str]:
    return await collect_experimental_generation_images(
        token=token,
        prompt=prompt,
        n=6,
        response_format="b64_json",
        aspect_ratio=aspect_ratio,
        concurrency=1,
    )


@router.websocket("/api/v1/admin/imagine/ws")
async def admin_imagine_ws(websocket: WebSocket):
    if not await _verify_ws_api_key(websocket):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    stop_event = asyncio.Event()
    run_task: Optional[asyncio.Task] = None

    async def _send(payload: dict) -> bool:
        try:
            await websocket.send_text(orjson.dumps(payload).decode())
            return True
        except Exception:
            return False

    async def _stop_run():
        nonlocal run_task
        stop_event.set()
        if run_task and not run_task.done():
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        run_task = None
        stop_event.clear()

    async def _run(prompt: str, aspect_ratio: str):
        model_id = "grok-imagine-1.0"
        model_info = ModelService.get(model_id)
        if not model_info or not model_info.is_image:
            await _send(
                {
                    "type": "error",
                    "message": "Image model is not available.",
                    "code": "model_not_supported",
                }
            )
            return

        token_mgr = await get_token_manager()
        sequence = 0
        run_id = uuid.uuid4().hex
        await _send(
            {
                "type": "status",
                "status": "running",
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "run_id": run_id,
            }
        )

        while not stop_event.is_set():
            try:
                await token_mgr.reload_if_stale()
                token = token_mgr.get_token_for_model(model_info.model_id)
                if not token:
                    await _send(
                        {
                            "type": "error",
                            "message": "No available tokens. Please try again later.",
                            "code": "rate_limit_exceeded",
                        }
                    )
                    await asyncio.sleep(2)
                    continue

                start_at = time.time()
                images = await _collect_imagine_batch(token, prompt, aspect_ratio)
                elapsed_ms = int((time.time() - start_at) * 1000)

                sent_any = False
                for image_b64 in images:
                    if not is_valid_imagine_image_value(image_b64):
                        continue
                    sent_any = True
                    sequence += 1
                    ok = await _send(
                        {
                            "type": "image",
                            "b64_json": image_b64,
                            "sequence": sequence,
                            "created_at": int(time.time() * 1000),
                            "elapsed_ms": elapsed_ms,
                            "aspect_ratio": aspect_ratio,
                            "run_id": run_id,
                        }
                    )
                    if not ok:
                        stop_event.set()
                        break

                if sent_any:
                    try:
                        await token_mgr.sync_usage(
                            token,
                            model_info.model_id,
                            consume_on_fail=True,
                            is_usage=True,
                        )
                    except Exception as e:
                        logger.warning(f"Imagine ws token sync failed: {e}")
                else:
                    await _send(
                        {
                            "type": "error",
                            "message": "Image generation returned empty data.",
                            "code": "empty_image",
                        }
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Imagine stream error: {e}")
                await _send(
                    {
                        "type": "error",
                        "message": str(e),
                        "code": "internal_error",
                    }
                )
                await asyncio.sleep(1.5)

        await _send({"type": "status", "status": "stopped", "run_id": run_id})

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except (RuntimeError, WebSocketDisconnect):
                break

            try:
                payload = orjson.loads(raw)
            except Exception:
                await _send(
                    {
                        "type": "error",
                        "message": "Invalid message format.",
                        "code": "invalid_payload",
                    }
                )
                continue

            msg_type = payload.get("type")
            if msg_type == "start":
                prompt = str(payload.get("prompt") or "").strip()
                if not prompt:
                    await _send(
                        {
                            "type": "error",
                            "message": "Prompt cannot be empty.",
                            "code": "empty_prompt",
                        }
                    )
                    continue
                ratio = resolve_imagine_aspect_ratio(str(payload.get("aspect_ratio") or "2:3").strip())
                await _stop_run()
                run_task = asyncio.create_task(_run(prompt, ratio))
            elif msg_type == "stop":
                await _stop_run()
            elif msg_type == "ping":
                await _send({"type": "pong"})
            else:
                await _send(
                    {
                        "type": "error",
                        "message": "Unknown command.",
                        "code": "unknown_command",
                    }
                )
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected by client")
    except asyncio.CancelledError:
        logger.debug("WebSocket handler cancelled")
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        await _stop_run()
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000, reason="Server closing connection")
        except Exception as e:
            logger.debug(f"WebSocket close ignored: {e}")


@router.post("/api/v1/admin/login")
async def admin_login_api(request: Request, body: AdminLoginBody | None = Body(default=None)):
    """管理后台登录验证（用户名+密码）

    - 默认账号/密码：admin/admin（可在配置管理的「应用设置」里修改）
    - 兼容旧版本：允许 Authorization: Bearer <password> 仅密码登录（用户名默认为 admin）
    """

    admin_username = str(get_config("app.admin_username", "admin") or "admin").strip() or "admin"
    admin_password = str(get_config("app.app_key", "admin") or "admin").strip()

    username = (body.username.strip() if body and isinstance(body.username, str) else "").strip()
    password = (body.password.strip() if body and isinstance(body.password, str) else "").strip()

    # Legacy: password-only via Bearer token.
    if not password:
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            password = auth[7:].strip()
            if not username:
                username = "admin"

    if not username or not password:
        raise HTTPException(status_code=400, detail="Missing username or password")

    if username != admin_username or password != admin_password:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    return {"status": "success", "api_key": get_config("app.api_key", "")}

@router.get("/api/v1/admin/config", dependencies=[Depends(verify_api_key)])
async def get_config_api():
    """获取当前配置"""
    # 暴露原始配置字典
    return config._config

@router.post("/api/v1/admin/config", dependencies=[Depends(verify_api_key)])
async def update_config_api(data: dict):
    """更新配置"""
    try:
        await config.update(data)
        return {"status": "success", "message": "配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _display_key(key: str) -> str:
    k = str(key or "")
    if len(k) <= 12:
        return k
    return f"{k[:6]}...{k[-4:]}"


def _normalize_limit(v: Any) -> int:
    if v is None or v == "":
        return -1
    try:
        return max(-1, int(v))
    except Exception:
        return -1


def _pool_to_token_type(pool_name: str) -> str:
    return "ssoSuper" if str(pool_name or "").strip() == "ssoSuper" else "sso"


def _parse_quota_value(v: Any) -> tuple[int, bool]:
    if v is None or v == "":
        return -1, False
    try:
        n = int(v)
    except Exception:
        return -1, False
    if n < 0:
        return -1, False
    return n, True


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _normalize_token_status(raw_status: Any) -> str:
    s = str(raw_status or "active").strip().lower()
    if s == "expired":
        return "invalid"
    if s in ("active", "cooling", "invalid", "disabled"):
        return s
    return "active"


def _normalize_admin_token_item(pool_name: str, item: Any) -> dict | None:
    token_type = _pool_to_token_type(pool_name)

    if isinstance(item, str):
        token = item.strip()
        if not token:
            return None
        if token.startswith("sso="):
            token = token[4:]
        return {
            "token": token,
            "status": "active",
            "quota": 0,
            "quota_known": False,
            "heavy_quota": -1,
            "heavy_quota_known": False,
            "token_type": token_type,
            "note": "",
            "fail_count": 0,
            "use_count": 0,
        }

    if not isinstance(item, dict):
        return None

    token = str(item.get("token") or "").strip()
    if not token:
        return None
    if token.startswith("sso="):
        token = token[4:]

    quota, quota_known = _parse_quota_value(item.get("quota"))
    heavy_quota, heavy_quota_known = _parse_quota_value(item.get("heavy_quota"))

    return {
        "token": token,
        "status": _normalize_token_status(item.get("status")),
        "quota": quota if quota_known else 0,
        "quota_known": quota_known,
        "heavy_quota": heavy_quota,
        "heavy_quota_known": heavy_quota_known,
        "token_type": token_type,
        "note": str(item.get("note") or ""),
        "fail_count": _safe_int(item.get("fail_count") or 0, 0),
        "use_count": _safe_int(item.get("use_count") or 0, 0),
    }


def _collect_tokens_from_pool_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []

    collected: list[str] = []
    seen: set[str] = set()
    for raw_items in payload.values():
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            token_raw = item if isinstance(item, str) else (item.get("token") if isinstance(item, dict) else "")
            token = normalize_refresh_token(str(token_raw or "").strip())
            if not token or token in seen:
                continue
            seen.add(token)
            collected.append(token)
    return collected


def _resolve_nsfw_refresh_concurrency(override: Any = None) -> int:
    source = override if override is not None else get_config("token.nsfw_refresh_concurrency", 10)
    try:
        value = int(source)
    except Exception:
        value = 10
    return max(1, value)


def _resolve_nsfw_refresh_retries(override: Any = None) -> int:
    source = override if override is not None else get_config("token.nsfw_refresh_retries", 3)
    try:
        value = int(source)
    except Exception:
        value = 3
    return max(0, value)


def _trigger_account_settings_refresh_background(
    tokens: list[str],
    concurrency: int,
    retries: int,
) -> None:
    if not tokens:
        return

    async def _run() -> None:
        try:
            result = await refresh_account_settings_for_tokens(
                tokens=tokens,
                concurrency=concurrency,
                retries=retries,
            )
            summary = result.get("summary") or {}
            logger.info(
                "Background account-settings refresh finished: total={} success={} failed={} invalidated={}",
                summary.get("total", 0),
                summary.get("success", 0),
                summary.get("failed", 0),
                summary.get("invalidated", 0),
            )
        except Exception as exc:
            logger.warning("Background account-settings refresh failed: {}", exc)

    asyncio.create_task(_run())


@router.get("/api/v1/admin/keys", dependencies=[Depends(verify_api_key)])
async def list_api_keys():
    """List API keys + daily usage/remaining (for admin UI)."""
    await api_key_manager.init()
    day, usage_map = await api_key_manager.usage_today()

    out = []
    for row in api_key_manager.get_all_keys():
        key = str(row.get("key") or "")
        used = usage_map.get(key) or {}
        chat_used = int(used.get("chat_used", 0) or 0)
        heavy_used = int(used.get("heavy_used", 0) or 0)
        image_used = int(used.get("image_used", 0) or 0)
        video_used = int(used.get("video_used", 0) or 0)

        chat_limit = _normalize_limit(row.get("chat_limit", -1))
        heavy_limit = _normalize_limit(row.get("heavy_limit", -1))
        image_limit = _normalize_limit(row.get("image_limit", -1))
        video_limit = _normalize_limit(row.get("video_limit", -1))

        remaining = {
            "chat": None if chat_limit < 0 else max(0, chat_limit - chat_used),
            "heavy": None if heavy_limit < 0 else max(0, heavy_limit - heavy_used),
            "image": None if image_limit < 0 else max(0, image_limit - image_used),
            "video": None if video_limit < 0 else max(0, video_limit - video_used),
        }

        out.append({
            **row,
            "is_active": bool(row.get("is_active", True)),
            "display_key": _display_key(key),
            "usage_today": {
                "chat_used": chat_used,
                "heavy_used": heavy_used,
                "image_used": image_used,
                "video_used": video_used,
            },
            "remaining_today": remaining,
            "day": day,
        })

    # New UI expects { success: true, data: [...] }
    return {"success": True, "data": out}


@router.post("/api/v1/admin/keys", dependencies=[Depends(verify_api_key)])
async def create_api_key(data: dict):
    """Create a new API key (optional name/key/limits)."""
    await api_key_manager.init()
    data = data or {}

    name = str(data.get("name") or "").strip() or api_key_manager.generate_name()
    key_val = str(data.get("key") or "").strip() or None
    is_active = bool(data.get("is_active", True))

    limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
    try:
        row = await api_key_manager.add_key(
            name=name,
            key=key_val,
            is_active=is_active,
            limits={
                "chat_per_day": limits.get("chat_per_day"),
                "heavy_per_day": limits.get("heavy_per_day"),
                "image_per_day": limits.get("image_per_day"),
                "video_per_day": limits.get("video_per_day"),
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, "data": {**row, "display_key": _display_key(row.get("key", ""))}}


@router.post("/api/v1/admin/keys/update", dependencies=[Depends(verify_api_key)])
async def update_api_key(data: dict):
    """Update name/status/limits for an API key."""
    await api_key_manager.init()
    data = data or {}
    key = str(data.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Missing key")

    existing = api_key_manager.get_key_row(key)
    if not existing:
        raise HTTPException(status_code=404, detail="Key not found")

    if "name" in data and data.get("name") is not None:
        name = str(data.get("name") or "").strip()
        if name:
            await api_key_manager.update_key_name(key, name)

    if "is_active" in data:
        await api_key_manager.update_key_status(key, bool(data.get("is_active")))

    limits = data.get("limits") if isinstance(data.get("limits"), dict) else None
    if limits is not None:
        await api_key_manager.update_key_limits(
            key,
            {
                "chat_per_day": limits.get("chat_per_day"),
                "heavy_per_day": limits.get("heavy_per_day"),
                "image_per_day": limits.get("image_per_day"),
                "video_per_day": limits.get("video_per_day"),
            },
        )

    return {"success": True}


@router.post("/api/v1/admin/keys/delete", dependencies=[Depends(verify_api_key)])
async def delete_api_key(data: dict):
    """Delete an API key."""
    await api_key_manager.init()
    data = data or {}
    key = str(data.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Missing key")

    ok = await api_key_manager.delete_key(key)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"success": True}

@router.get("/api/v1/admin/storage", dependencies=[Depends(verify_api_key)])
async def get_storage_info():
    """获取当前存储模式"""
    storage_type = os.getenv("SERVER_STORAGE_TYPE", "local").lower()
    logger.info(f"Storage type: {storage_type}")
    if not storage_type:
        storage_type = str(get_config("storage.type", "")).lower()
    if not storage_type:
        storage = get_storage()
        if isinstance(storage, LocalStorage):
            storage_type = "local"
        elif isinstance(storage, RedisStorage):
            storage_type = "redis"
        elif isinstance(storage, SQLStorage):
            if storage.dialect in ("mysql", "mariadb"):
                storage_type = "mysql"
            elif storage.dialect in ("postgres", "postgresql", "pgsql"):
                storage_type = "pgsql"
            else:
                storage_type = storage.dialect
    return {"type": storage_type or "local"}

@router.get("/api/v1/admin/tokens", dependencies=[Depends(verify_api_key)])
async def get_tokens_api():
    """获取所有 Token"""
    storage = get_storage()
    tokens = await storage.load_tokens()
    data = tokens if isinstance(tokens, dict) else {}
    out: dict[str, list[dict]] = {}
    for pool_name, raw_items in data.items():
        arr = raw_items if isinstance(raw_items, list) else []
        normalized: list[dict] = []
        for item in arr:
            obj = _normalize_admin_token_item(pool_name, item)
            if obj:
                normalized.append(obj)
        out[str(pool_name)] = normalized
    return out

@router.post("/api/v1/admin/tokens", dependencies=[Depends(verify_api_key)])
async def update_tokens_api(data: dict):
    """Update token payload and trigger background account-settings refresh for new tokens."""
    storage = get_storage()
    try:
        from app.services.token.manager import get_token_manager

        posted_data = data if isinstance(data, dict) else {}
        existing_tokens: list[str] = []
        added_tokens: list[str] = []

        async with storage.acquire_lock("tokens_save", timeout=10):
            old_data = await storage.load_tokens()
            existing_tokens = _collect_tokens_from_pool_payload(
                old_data if isinstance(old_data, dict) else {}
            )

            await storage.save_tokens(posted_data)
            mgr = await get_token_manager()
            await mgr.reload()

            new_tokens = _collect_tokens_from_pool_payload(posted_data)
            existing_set = set(existing_tokens)
            added_tokens = [token for token in new_tokens if token not in existing_set]

        concurrency = _resolve_nsfw_refresh_concurrency()
        retries = _resolve_nsfw_refresh_retries()
        _trigger_account_settings_refresh_background(
            tokens=added_tokens,
            concurrency=concurrency,
            retries=retries,
        )

        return {
            "status": "success",
            "message": "Token updated",
            "nsfw_refresh": {
                "mode": "background",
                "triggered": len(added_tokens),
                "concurrency": concurrency,
                "retries": retries,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/v1/admin/tokens/refresh", dependencies=[Depends(verify_api_key)])
async def refresh_tokens_api(data: dict):
    """刷新 Token 状态"""
    from app.services.token.manager import get_token_manager
    
    try:
        mgr = await get_token_manager()
        tokens = []
        if "token" in data:
            tokens.append(data["token"])
        if "tokens" in data and isinstance(data["tokens"], list):
            tokens.extend(data["tokens"])
            
        if not tokens:
             raise HTTPException(status_code=400, detail="No tokens provided")
             
        unique_tokens = list(set(tokens))
        
        sem = asyncio.Semaphore(10)
        
        async def _refresh_one(t):
            async with sem:
                return t, await mgr.sync_usage(t, "grok-3", consume_on_fail=False, is_usage=False)
        
        results_list = await asyncio.gather(*[_refresh_one(t) for t in unique_tokens])
        results = dict(results_list)
            
        return {"status": "success", "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/v1/admin/tokens/nsfw/refresh", dependencies=[Depends(verify_api_key)])
async def refresh_tokens_nsfw_api(data: dict):
    """Refresh account settings (TOS + birth date + NSFW) for selected/all tokens."""
    payload = data if isinstance(data, dict) else {}
    mgr = await get_token_manager()

    tokens: list[str] = []
    seen: set[str] = set()

    if bool(payload.get("all")):
        for pool in mgr.pools.values():
            for info in pool.list():
                token = normalize_refresh_token(str(info.token or "").strip())
                if not token or token in seen:
                    continue
                seen.add(token)
                tokens.append(token)
    else:
        candidates: list[str] = []
        single = payload.get("token")
        if isinstance(single, str):
            candidates.append(single)
        batch = payload.get("tokens")
        if isinstance(batch, list):
            candidates.extend([item for item in batch if isinstance(item, str)])

        for raw in candidates:
            token = normalize_refresh_token(str(raw or "").strip())
            if not token or token in seen:
                continue
            seen.add(token)
            tokens.append(token)

    if not tokens:
        raise HTTPException(status_code=400, detail="No tokens provided")

    concurrency = _resolve_nsfw_refresh_concurrency(payload.get("concurrency"))
    retries = _resolve_nsfw_refresh_retries(payload.get("retries"))
    result = await refresh_account_settings_for_tokens(
        tokens=tokens,
        concurrency=concurrency,
        retries=retries,
    )
    return {
        "status": "success",
        "summary": result.get("summary") or {},
        "failed": result.get("failed") or [],
    }


@router.post("/api/v1/admin/tokens/auto-register", dependencies=[Depends(verify_api_key)])
async def auto_register_tokens_api(data: dict):
    """Start auto registration."""
    try:
        data = data or {}
        count = data.get("count")
        concurrency = data.get("concurrency")
        pool = (data.get("pool") or "ssoBasic").strip() or "ssoBasic"

        try:
            count_val = int(count)
        except Exception:
            count_val = int(get_config("register.default_count", 100) or 100)

        if count_val <= 0:
            count_val = int(get_config("register.default_count", 100) or 100)

        try:
            concurrency_val = int(concurrency)
        except Exception:
            concurrency_val = None
        if concurrency_val is not None and concurrency_val <= 0:
            concurrency_val = None

        manager = get_auto_register_manager()
        job = await manager.start_job(count=count_val, pool=pool, concurrency=concurrency_val)
        return {"status": "started", "job": job.to_dict()}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/admin/tokens/auto-register/status", dependencies=[Depends(verify_api_key)])
async def auto_register_status_api(job_id: str | None = None):
    """Get auto registration status."""
    manager = get_auto_register_manager()
    status = manager.get_status(job_id)
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@router.post("/api/v1/admin/tokens/auto-register/stop", dependencies=[Depends(verify_api_key)])
async def auto_register_stop_api(job_id: str | None = None):
    """Stop auto registration (best-effort)."""
    manager = get_auto_register_manager()
    status = manager.get_status(job_id)
    if status.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Job not found")
    await manager.stop_job()
    return {"status": "stopping"}

@router.get("/admin/cache", response_class=HTMLResponse, include_in_schema=False)
async def admin_cache_page():
    """缓存管理页"""
    return await render_template("cache/cache.html")

@router.get("/api/v1/admin/cache", dependencies=[Depends(verify_api_key)])
async def get_cache_stats_api(request: Request):
    """获取缓存统计"""
    from app.services.grok.assets import DownloadService, ListService
    from app.services.token.manager import get_token_manager
    
    try:
        dl_service = DownloadService()
        image_stats = dl_service.get_stats("image")
        video_stats = dl_service.get_stats("video")
        
        mgr = await get_token_manager()
        pools = mgr.pools
        accounts = []
        for pool_name, pool in pools.items():
            for info in pool.list():
                raw_token = info.token[4:] if info.token.startswith("sso=") else info.token
                masked = f"{raw_token[:8]}...{raw_token[-16:]}" if len(raw_token) > 24 else raw_token
                accounts.append({
                    "token": raw_token,
                    "token_masked": masked,
                    "pool": pool_name,
                    "status": info.status,
                    "last_asset_clear_at": info.last_asset_clear_at
                })

        scope = request.query_params.get("scope")
        selected_token = request.query_params.get("token")
        tokens_param = request.query_params.get("tokens")
        selected_tokens = []
        if tokens_param:
            selected_tokens = [t.strip() for t in tokens_param.split(",") if t.strip()]

        online_stats = {"count": 0, "status": "unknown", "token": None, "last_asset_clear_at": None}
        online_details = []
        account_map = {a["token"]: a for a in accounts}
        batch_size = get_config("performance.admin_assets_batch_size", 10)
        try:
            batch_size = int(batch_size)
        except Exception:
            batch_size = 10
        batch_size = max(1, batch_size)

        async def _fetch_assets(token: str):
            list_service = ListService()
            try:
                return await list_service.count(token)
            finally:
                await list_service.close()

        async def _fetch_detail(token: str):
            account = account_map.get(token)
            try:
                count = await _fetch_assets(token)
                return ({
                    "token": token,
                    "token_masked": account["token_masked"] if account else token,
                    "count": count,
                    "status": "ok",
                    "last_asset_clear_at": account["last_asset_clear_at"] if account else None
                }, count)
            except Exception as e:
                return ({
                    "token": token,
                    "token_masked": account["token_masked"] if account else token,
                    "count": 0,
                    "status": f"error: {str(e)}",
                    "last_asset_clear_at": account["last_asset_clear_at"] if account else None
                }, 0)

        if selected_tokens:
            total = 0
            for i in range(0, len(selected_tokens), batch_size):
                chunk = selected_tokens[i:i + batch_size]
                results = await asyncio.gather(*[_fetch_detail(token) for token in chunk])
                for detail, count in results:
                    online_details.append(detail)
                    total += count
            online_stats = {"count": total, "status": "ok" if selected_tokens else "no_token", "token": None, "last_asset_clear_at": None}
            scope = "selected"
        elif scope == "all":
            total = 0
            tokens = [account["token"] for account in accounts]
            for i in range(0, len(tokens), batch_size):
                chunk = tokens[i:i + batch_size]
                results = await asyncio.gather(*[_fetch_detail(token) for token in chunk])
                for detail, count in results:
                    online_details.append(detail)
                    total += count
            online_stats = {"count": total, "status": "ok" if accounts else "no_token", "token": None, "last_asset_clear_at": None}
        else:
            token = selected_token
            if token:
                try:
                    count = await _fetch_assets(token)
                    match = next((a for a in accounts if a["token"] == token), None)
                    online_stats = {
                        "count": count,
                        "status": "ok",
                        "token": token,
                        "token_masked": match["token_masked"] if match else token,
                        "last_asset_clear_at": match["last_asset_clear_at"] if match else None
                    }
                except Exception as e:
                    match = next((a for a in accounts if a["token"] == token), None)
                    online_stats = {
                        "count": 0,
                        "status": f"error: {str(e)}",
                        "token": token,
                        "token_masked": match["token_masked"] if match else token,
                        "last_asset_clear_at": match["last_asset_clear_at"] if match else None
                    }
            else:
                online_stats = {"count": 0, "status": "not_loaded", "token": None, "last_asset_clear_at": None}
            
        return {
            "local_image": image_stats,
            "local_video": video_stats,
            "online": online_stats,
            "online_accounts": accounts,
            "online_scope": scope or "none",
            "online_details": online_details
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/v1/admin/cache/clear", dependencies=[Depends(verify_api_key)])
async def clear_local_cache_api(data: dict):
    """清理本地缓存"""
    from app.services.grok.assets import DownloadService
    cache_type = data.get("type", "image")
    
    try:
        dl_service = DownloadService()
        result = dl_service.clear(cache_type)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/v1/admin/cache/list", dependencies=[Depends(verify_api_key)])
async def list_local_cache_api(
    cache_type: str = "image",
    type_: str = Query(default=None, alias="type"),
    page: int = 1,
    page_size: int = 1000
):
    """列出本地缓存文件"""
    from app.services.grok.assets import DownloadService
    try:
        if type_:
            cache_type = type_
        dl_service = DownloadService()
        result = dl_service.list_files(cache_type, page, page_size)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/v1/admin/cache/item/delete", dependencies=[Depends(verify_api_key)])
async def delete_local_cache_item_api(data: dict):
    """删除单个本地缓存文件"""
    from app.services.grok.assets import DownloadService
    cache_type = data.get("type", "image")
    name = data.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Missing file name")
    try:
        dl_service = DownloadService()
        result = dl_service.delete_file(cache_type, name)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/v1/admin/cache/online/clear", dependencies=[Depends(verify_api_key)])
async def clear_online_cache_api(data: dict):
    """清理在线缓存"""
    from app.services.grok.assets import DeleteService
    from app.services.token.manager import get_token_manager
    
    delete_service = None
    try:
        mgr = await get_token_manager()
        tokens = data.get("tokens")
        delete_service = DeleteService()

        if isinstance(tokens, list):
            token_list = [t.strip() for t in tokens if isinstance(t, str) and t.strip()]
            if not token_list:
                raise HTTPException(status_code=400, detail="No tokens provided")

            results = {}
            batch_size = get_config("performance.admin_assets_batch_size", 10)
            try:
                batch_size = int(batch_size)
            except Exception:
                batch_size = 10
            batch_size = max(1, batch_size)

            async def _clear_one(t: str):
                try:
                    result = await delete_service.delete_all(t)
                    await mgr.mark_asset_clear(t)
                    return t, {"status": "success", "result": result}
                except Exception as e:
                    return t, {"status": "error", "error": str(e)}

            for i in range(0, len(token_list), batch_size):
                chunk = token_list[i:i + batch_size]
                res_list = await asyncio.gather(*[_clear_one(t) for t in chunk])
                for t, res in res_list:
                    results[t] = res

            return {"status": "success", "results": results}

        token = data.get("token") or mgr.get_token()
        if not token:
            raise HTTPException(status_code=400, detail="No available token to perform cleanup")

        result = await delete_service.delete_all(token)
        await mgr.mark_asset_clear(token)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if delete_service:
            await delete_service.close()


@router.get("/api/v1/admin/metrics", dependencies=[Depends(verify_api_key)])
async def get_metrics_api():
    """数据中心：聚合常用指标（token/cache/request_stats）。"""
    try:
        from app.services.request_stats import request_stats
        from app.services.token.manager import get_token_manager
        from app.services.token.models import TokenStatus
        from app.services.grok.assets import DownloadService

        mgr = await get_token_manager()
        await mgr.reload_if_stale()

        total = 0
        active = 0
        cooling = 0
        expired = 0
        disabled = 0
        chat_quota = 0
        total_calls = 0

        for pool in mgr.pools.values():
            for info in pool.list():
                total += 1
                total_calls += int(getattr(info, "use_count", 0) or 0)
                if info.status == TokenStatus.ACTIVE:
                    active += 1
                    chat_quota += int(getattr(info, "quota", 0) or 0)
                elif info.status == TokenStatus.COOLING:
                    cooling += 1
                elif info.status == TokenStatus.EXPIRED:
                    expired += 1
                elif info.status == TokenStatus.DISABLED:
                    disabled += 1

        dl = DownloadService()
        local_image = dl.get_stats("image")
        local_video = dl.get_stats("video")

        await request_stats.init()
        stats = request_stats.get_stats(hours=24, days=7)

        return {
            "tokens": {
                "total": total,
                "active": active,
                "cooling": cooling,
                "expired": expired,
                "disabled": disabled,
                "chat_quota": chat_quota,
                "image_quota": int(chat_quota // 2),
                "total_calls": total_calls,
            },
            "cache": {
                "local_image": local_image,
                "local_video": local_video,
            },
            "request_stats": stats,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/admin/cache/local", dependencies=[Depends(verify_api_key)])
async def get_cache_local_stats_api():
    """仅获取本地缓存统计（用于前端实时刷新）。"""
    from app.services.grok.assets import DownloadService

    try:
        dl_service = DownloadService()
        image_stats = dl_service.get_stats("image")
        video_stats = dl_service.get_stats("video")
        return {"local_image": image_stats, "local_video": video_stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _safe_log_file_path(name: str) -> Path:
    """Resolve a log file name under ./logs safely."""
    from app.core.logger import LOG_DIR

    name = (name or "").strip()
    if not name:
        raise ValueError("Missing log file")
    # Disallow path traversal.
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid log file name")

    p = (LOG_DIR / name).resolve()
    if LOG_DIR.resolve() not in p.parents:
        raise ValueError("Invalid log file path")
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(name)
    return p


def _format_log_line(raw: str) -> str:
    raw = (raw or "").rstrip("\r\n")
    if not raw:
        return ""

    # Try JSON log line (our file sink uses json lines).
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return raw
        ts = str(obj.get("time", "") or "")
        ts = ts.replace("T", " ")
        if len(ts) >= 19:
            ts = ts[:19]
        level = str(obj.get("level", "") or "").upper()
        caller = str(obj.get("caller", "") or "")
        msg = str(obj.get("msg", "") or "")
        if not (ts and level and msg):
            return raw
        return f"{ts} | {level:<8} | {caller} - {msg}".rstrip()
    except Exception:
        return raw


def _tail_lines(path: Path, max_lines: int = 2000, max_bytes: int = 1024 * 1024) -> list[str]:
    """Best-effort tail for a text file."""
    try:
        max_lines = int(max_lines)
    except Exception:
        max_lines = 2000
    max_lines = max(1, min(5000, max_lines))
    max_bytes = max(16 * 1024, min(5 * 1024 * 1024, int(max_bytes)))

    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        start = max(0, end - max_bytes)
        f.seek(start, os.SEEK_SET)
        data = f.read()

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # If we read from the middle of a line, drop the first partial line.
    if start > 0 and lines:
        lines = lines[1:]
    lines = lines[-max_lines:]
    return [_format_log_line(ln) for ln in lines if ln is not None]


@router.get("/api/v1/admin/logs/files", dependencies=[Depends(verify_api_key)])
async def list_log_files_api():
    """列出可查看的日志文件（logs/*.log）。"""
    from app.core.logger import LOG_DIR

    try:
        items = []
        for p in LOG_DIR.glob("*.log"):
            try:
                stat = p.stat()
                items.append(
                    {
                        "name": p.name,
                        "size_bytes": stat.st_size,
                        "mtime_ms": int(stat.st_mtime * 1000),
                    }
                )
            except Exception:
                continue
        items.sort(key=lambda x: x["mtime_ms"], reverse=True)
        return {"files": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/v1/admin/logs/tail", dependencies=[Depends(verify_api_key)])
async def tail_log_api(file: str | None = None, lines: int = 500):
    """读取后台日志（尾部）。"""
    from app.core.logger import LOG_DIR

    try:
        # Default to latest log.
        if not file:
            candidates = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            if not candidates:
                return {"file": None, "lines": []}
            path = candidates[0]
            file = path.name
        else:
            path = _safe_log_file_path(file)

        data = await asyncio.to_thread(_tail_lines, path, lines)
        return {"file": str(file), "lines": data}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Log file not found")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

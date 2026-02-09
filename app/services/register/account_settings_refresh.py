from __future__ import annotations

import asyncio
from typing import Iterable, Any

from app.core.config import get_config
from app.core.logger import logger
from app.services.register.services import (
    UserAgreementService,
    BirthDateService,
    NsfwSettingsService,
)
from app.services.token.manager import TokenManager, get_token_manager


DEFAULT_NSFW_REFRESH_CONCURRENCY = 10
DEFAULT_NSFW_REFRESH_RETRIES = 3
DEFAULT_IMPERSONATE = "chrome120"


def _extract_cookie_value(cookie_str: str, name: str) -> str | None:
    needle = f"{name}="
    if needle not in cookie_str:
        return None
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith(needle):
            value = part[len(needle):].strip()
            return value or None
    return None


def parse_sso_pair(raw_token: str) -> tuple[str, str]:
    raw = str(raw_token or "").strip()
    if not raw:
        return "", ""

    if ";" in raw:
        sso = _extract_cookie_value(raw, "sso") or ""
        sso_rw = _extract_cookie_value(raw, "sso-rw") or sso
        return sso.strip(), sso_rw.strip()

    sso = raw[4:].strip() if raw.startswith("sso=") else raw
    sso_rw = sso
    return sso, sso_rw


def normalize_sso_token(raw_token: str) -> str:
    sso, _ = parse_sso_pair(raw_token)
    return sso


def _coerce_concurrency(value: Any, default: int = DEFAULT_NSFW_REFRESH_CONCURRENCY) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(1, n)


def _coerce_retries(value: Any, default: int = DEFAULT_NSFW_REFRESH_RETRIES) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(0, n)


def _format_step_error(result: dict, fallback: str = "unknown error") -> str:
    if not isinstance(result, dict):
        return fallback

    text = str(result.get("error") or "").strip()
    if text:
        return text

    status_code = result.get("status_code")
    if status_code is not None:
        return f"HTTP {status_code}"

    grpc_status = result.get("grpc_status")
    if grpc_status is not None:
        return f"gRPC {grpc_status}"

    response_text = str(result.get("response_text") or "").strip()
    if response_text:
        return response_text

    return fallback


class AccountSettingsRefreshService:
    def __init__(self, token_manager: TokenManager, cf_clearance: str = "") -> None:
        self.token_manager = token_manager
        self.cf_clearance = (cf_clearance or "").strip()

    def _apply_once(self, raw_token: str) -> tuple[bool, str, str]:
        sso, sso_rw = parse_sso_pair(raw_token)
        if not sso:
            return False, "parse", "missing sso"
        if not sso_rw:
            sso_rw = sso

        user_service = UserAgreementService(cf_clearance=self.cf_clearance)
        birth_service = BirthDateService(cf_clearance=self.cf_clearance)
        nsfw_service = NsfwSettingsService(cf_clearance=self.cf_clearance)

        tos_result = user_service.accept_tos_version(
            sso=sso,
            sso_rw=sso_rw,
            impersonate=DEFAULT_IMPERSONATE,
        )
        if not tos_result.get("ok"):
            return False, "tos", _format_step_error(tos_result, "accept_tos failed")

        birth_result = birth_service.set_birth_date(
            sso=sso,
            sso_rw=sso_rw,
            impersonate=DEFAULT_IMPERSONATE,
        )
        if not birth_result.get("ok"):
            return False, "birth", _format_step_error(birth_result, "set_birth_date failed")

        nsfw_result = nsfw_service.enable_nsfw(
            sso=sso,
            sso_rw=sso_rw,
            impersonate=DEFAULT_IMPERSONATE,
        )
        if not nsfw_result.get("ok"):
            return False, "nsfw", _format_step_error(nsfw_result, "enable_nsfw failed")

        return True, "", ""

    async def refresh_tokens(
        self,
        tokens: Iterable[str],
        concurrency: int = DEFAULT_NSFW_REFRESH_CONCURRENCY,
        retries: int = DEFAULT_NSFW_REFRESH_RETRIES,
    ) -> dict[str, Any]:
        resolved_concurrency = _coerce_concurrency(concurrency)
        resolved_retries = _coerce_retries(retries)

        unique_tokens: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            normalized = normalize_sso_token(str(token or "").strip())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_tokens.append(normalized)

        if not unique_tokens:
            return {
                "summary": {"total": 0, "success": 0, "failed": 0, "invalidated": 0},
                "failed": [],
            }

        semaphore = asyncio.Semaphore(resolved_concurrency)

        async def _run_one(token: str) -> dict[str, Any]:
            max_attempts = resolved_retries + 1
            last_step = "unknown"
            last_error = "unknown error"

            async with semaphore:
                for attempt in range(1, max_attempts + 1):
                    try:
                        ok, step, error = await asyncio.to_thread(self._apply_once, token)
                    except Exception as exc:
                        ok, step, error = False, "exception", str(exc)

                    if ok:
                        updated = await self.token_manager.mark_token_account_settings_success(
                            token,
                            save=False,
                        )
                        if not updated:
                            logger.warning(
                                "Account settings refresh succeeded but token not found: {}...",
                                token[:10],
                            )
                        return {
                            "token": token,
                            "ok": True,
                            "attempts": attempt,
                        }

                    last_step = step or "unknown"
                    last_error = error or "unknown error"

                reason = (
                    f"account_settings_refresh_failed step={last_step} "
                    f"attempts={max_attempts} error={last_error}"
                )
                invalidated = await self.token_manager.set_token_invalid(
                    token,
                    reason=reason,
                    save=False,
                )
                return {
                    "token": token,
                    "ok": False,
                    "attempts": max_attempts,
                    "step": last_step,
                    "error": last_error,
                    "invalidated": bool(invalidated),
                }

        results = await asyncio.gather(*[_run_one(token) for token in unique_tokens])

        try:
            await self.token_manager.commit()
        except Exception as exc:
            logger.warning("Account settings refresh commit failed: {}", exc)

        success = sum(1 for item in results if item.get("ok"))
        failed_items = [item for item in results if not item.get("ok")]
        invalidated = sum(1 for item in failed_items if item.get("invalidated"))

        summary = {
            "total": len(unique_tokens),
            "success": success,
            "failed": len(failed_items),
            "invalidated": invalidated,
        }

        return {"summary": summary, "failed": failed_items}


async def refresh_account_settings_for_tokens(
    tokens: Iterable[str],
    concurrency: int | None = None,
    retries: int | None = None,
) -> dict[str, Any]:
    resolved_concurrency = _coerce_concurrency(
        concurrency if concurrency is not None else get_config(
            "token.nsfw_refresh_concurrency",
            DEFAULT_NSFW_REFRESH_CONCURRENCY,
        ),
        default=DEFAULT_NSFW_REFRESH_CONCURRENCY,
    )
    resolved_retries = _coerce_retries(
        retries if retries is not None else get_config(
            "token.nsfw_refresh_retries",
            DEFAULT_NSFW_REFRESH_RETRIES,
        ),
        default=DEFAULT_NSFW_REFRESH_RETRIES,
    )

    token_manager = await get_token_manager()
    cf_clearance = str(get_config("grok.cf_clearance", "") or "").strip()
    service = AccountSettingsRefreshService(token_manager, cf_clearance=cf_clearance)
    return await service.refresh_tokens(
        tokens=tokens,
        concurrency=resolved_concurrency,
        retries=resolved_retries,
    )


__all__ = [
    "AccountSettingsRefreshService",
    "parse_sso_pair",
    "normalize_sso_token",
    "refresh_account_settings_for_tokens",
    "DEFAULT_NSFW_REFRESH_CONCURRENCY",
    "DEFAULT_NSFW_REFRESH_RETRIES",
]

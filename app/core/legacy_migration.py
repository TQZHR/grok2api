"""
Legacy data migrations for local deployments (python/docker).

Goal: when upgrading the project, old on-disk data should still be readable and not lost.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict

from app.core.logger import logger


def migrate_legacy_cache_dirs(data_dir: Path | None = None) -> Dict[str, Any]:
    """
    Migrate old cache directory layout:

    - legacy: data/temp/{image,video}
    - current: data/tmp/{image,video}

    This keeps existing cached files (not yet cleaned) available after upgrades.
    """

    data_root = data_dir or (Path(__file__).parent.parent.parent / "data")
    legacy_root = data_root / "temp"
    current_root = data_root / "tmp"

    if not legacy_root.exists() or not legacy_root.is_dir():
        return {"migrated": False, "reason": "no_legacy_dir"}

    lock_dir = data_root / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    done_marker = lock_dir / "legacy_cache_dirs_v1.done"
    if done_marker.exists():
        return {"migrated": False, "reason": "already_done"}

    lock_file = lock_dir / "legacy_cache_dirs_v1.lock"

    # Best-effort cross-process lock (works on Windows/Linux).
    fd: int | None = None
    try:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Another worker/process is migrating. Wait briefly for completion.
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if done_marker.exists():
                    return {"migrated": False, "reason": "waited_for_other_process"}
                time.sleep(0.2)
            return {"migrated": False, "reason": "lock_timeout"}

        current_root.mkdir(parents=True, exist_ok=True)

        moved = 0
        skipped = 0
        errors = 0

        for sub in ("image", "video"):
            src_dir = legacy_root / sub
            if not src_dir.exists() or not src_dir.is_dir():
                continue

            dst_dir = current_root / sub
            dst_dir.mkdir(parents=True, exist_ok=True)

            for item in src_dir.iterdir():
                if not item.is_file():
                    continue
                target = dst_dir / item.name
                if target.exists():
                    skipped += 1
                    continue
                try:
                    shutil.move(str(item), str(target))
                    moved += 1
                except Exception:
                    errors += 1

        # Cleanup empty legacy dirs (best-effort).
        for sub in ("image", "video"):
            p = legacy_root / sub
            try:
                if p.exists() and p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            except Exception:
                pass
        try:
            if legacy_root.exists() and legacy_root.is_dir() and not any(legacy_root.iterdir()):
                legacy_root.rmdir()
        except Exception:
            pass

        if errors == 0:
            done_marker.write_text(str(int(time.time())), encoding="utf-8")
        if moved or skipped or errors:
            logger.info(
                f"Legacy cache migration complete: moved={moved}, skipped={skipped}, errors={errors}"
            )
        return {"migrated": True, "moved": moved, "skipped": skipped, "errors": errors}
    finally:
        try:
            if fd is not None:
                os.close(fd)
        except Exception:
            pass
        try:
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass


__all__ = ["migrate_legacy_cache_dirs", "migrate_legacy_account_settings"]


async def migrate_legacy_account_settings(
    concurrency: int = 10,
    data_dir: Path | None = None,
) -> Dict[str, Any]:
    """
    After legacy data migration, run a one-time TOS + BirthDate + NSFW pass for existing accounts.

    This is best-effort and guarded by a cross-process lock + done marker.
    """

    data_root = data_dir or (Path(__file__).parent.parent.parent / "data")
    lock_dir = data_root / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    done_marker = lock_dir / "legacy_accounts_tos_birth_nsfw_v2.done"
    if done_marker.exists():
        return {"migrated": False, "reason": "already_done"}

    lock_file = lock_dir / "legacy_accounts_tos_birth_nsfw_v2.lock"
    fd: int | None = None

    try:
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if done_marker.exists():
                    return {"migrated": False, "reason": "waited_for_other_process"}
                await asyncio.sleep(0.2)
            return {"migrated": False, "reason": "lock_timeout"}

        from app.core.config import get_config
        from app.core.storage import get_storage
        from app.services.register.services import (
            UserAgreementService,
            BirthDateService,
            NsfwSettingsService,
        )

        storage = get_storage()
        try:
            token_data = await storage.load_tokens()
        except Exception as exc:
            logger.warning("Legacy account migration: failed to load tokens: {}", exc)
            return {"migrated": False, "reason": "load_tokens_failed"}

        token_data = token_data or {}
        tokens: list[str] = []
        for items in token_data.values():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    tokens.append(item)
                elif isinstance(item, dict):
                    token_val = item.get("token")
                    if isinstance(token_val, str):
                        tokens.append(token_val)

        # De-duplicate while preserving order.
        tokens = list(dict.fromkeys([t.strip() for t in tokens if isinstance(t, str) and t.strip()]))
        if not tokens:
            done_marker.write_text(str(int(time.time())), encoding="utf-8")
            return {"migrated": True, "total": 0, "ok": 0, "failed": 0}

        try:
            concurrency = max(1, int(concurrency))
        except Exception:
            concurrency = 10

        cf_clearance = str(get_config("grok.cf_clearance", "") or "").strip()

        def _extract_cookie_value(cookie_str: str, name: str) -> str | None:
            needle = f"{name}="
            if needle not in cookie_str:
                return None
            for part in cookie_str.split(";"):
                part = part.strip()
                if part.startswith(needle):
                    return part[len(needle):].strip()
            return None

        def _normalize_tokens(raw_token: str) -> tuple[str, str]:
            raw_token = raw_token.strip()
            if ";" in raw_token:
                sso_val = _extract_cookie_value(raw_token, "sso") or ""
                sso_rw_val = _extract_cookie_value(raw_token, "sso-rw") or sso_val
            else:
                sso_val = raw_token[4:] if raw_token.startswith("sso=") else raw_token
                sso_rw_val = sso_val
            return sso_val, sso_rw_val

        def _apply_settings(raw_token: str) -> bool:
            sso_val, sso_rw_val = _normalize_tokens(raw_token)
            if not sso_val:
                return False

            user_service = UserAgreementService(cf_clearance=cf_clearance)
            birth_service = BirthDateService(cf_clearance=cf_clearance)
            nsfw_service = NsfwSettingsService(cf_clearance=cf_clearance)

            tos_result = user_service.accept_tos_version(
                sso=sso_val,
                sso_rw=sso_rw_val or sso_val,
                impersonate="chrome120",
            )
            if not tos_result.get("ok"):
                return False

            birth_result = birth_service.set_birth_date(
                sso=sso_val,
                sso_rw=sso_rw_val or sso_val,
                impersonate="chrome120",
            )
            if not birth_result.get("ok"):
                return False

            nsfw_result = nsfw_service.enable_nsfw(
                sso=sso_val,
                sso_rw=sso_rw_val or sso_val,
                impersonate="chrome120",
            )
            return bool(nsfw_result.get("ok"))

        sem = asyncio.Semaphore(concurrency)

        async def _run_one(token: str) -> bool:
            async with sem:
                return await asyncio.to_thread(_apply_settings, token)

        tasks = [_run_one(token) for token in tokens]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        ok = 0
        failed = 0
        for res in results:
            if isinstance(res, Exception):
                failed += 1
            elif res:
                ok += 1
            else:
                failed += 1

        done_marker.write_text(str(int(time.time())), encoding="utf-8")
        logger.info(
            "Legacy account migration complete: total=%d, ok=%d, failed=%d",
            len(tokens),
            ok,
            failed,
        )
        return {"migrated": True, "total": len(tokens), "ok": ok, "failed": failed}
    finally:
        try:
            if fd is not None:
                os.close(fd)
        except Exception:
            pass
        try:
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass

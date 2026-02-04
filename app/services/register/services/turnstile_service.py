"""Turnstile solving service."""
from __future__ import annotations

import os
import time
from typing import Optional

from app.core.logger import logger

import requests

from app.core.config import get_config


class TurnstileService:
    """Turnstile solver wrapper (local solver or YesCaptcha)."""

    def __init__(
        self,
        solver_url: Optional[str] = None,
        yescaptcha_key: Optional[str] = None,
    ) -> None:
        self.yescaptcha_key = (
            (yescaptcha_key or get_config("register.yescaptcha_key", "") or os.getenv("YESCAPTCHA_KEY", "")).strip()
        )
        self.solver_url = (
            solver_url
            or get_config("register.solver_url", "")
            or os.getenv("TURNSTILE_SOLVER_URL", "")
            or "http://127.0.0.1:5072"
        ).strip()
        self.yescaptcha_api = "https://api.yescaptcha.com"
        self.last_error: Optional[str] = None

    def create_task(self, siteurl: str, sitekey: str) -> str:
        """Create a Turnstile task and return task ID."""
        self.last_error = None
        if self.yescaptcha_key:
            url = f"{self.yescaptcha_api}/createTask"
            payload = {
                "clientKey": self.yescaptcha_key,
                "task": {
                    "type": "TurnstileTaskProxyless",
                    "websiteURL": siteurl,
                    "websiteKey": sitekey,
                },
            }
            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data.get("errorId") != 0:
                desc = data.get("errorDescription") or "unknown"
                self.last_error = f"YesCaptcha createTask failed: {desc}"
                raise RuntimeError(self.last_error)
            return data["taskId"]

        response = requests.get(
            f"{self.solver_url}/turnstile",
            params={"url": siteurl, "sitekey": sitekey},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        task_id = data.get("taskId")
        if not task_id:
            self.last_error = data.get("errorDescription") or data.get("errorCode") or "missing taskId"
            raise RuntimeError(f"Solver create task failed: {self.last_error}")
        return task_id

    def get_response(
        self,
        task_id: str,
        max_retries: int = 30,
        initial_delay: int = 5,
        retry_delay: int = 2,
        stop_event: object | None = None,
    ) -> Optional[str]:
        """Fetch a Turnstile solution token."""
        self.last_error = None
        # Make shutdown/cancel responsive.
        if initial_delay > 0:
            for _ in range(int(initial_delay * 10)):
                if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                    return None
                time.sleep(0.1)

        for _ in range(max_retries):
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                return None
            try:
                if self.yescaptcha_key:
                    url = f"{self.yescaptcha_api}/getTaskResult"
                    payload = {"clientKey": self.yescaptcha_key, "taskId": task_id}
                    response = requests.post(url, json=payload, timeout=20)
                    response.raise_for_status()
                    data = response.json()

                    if data.get("errorId") != 0:
                        self.last_error = str(data.get("errorDescription") or "unknown")
                        logger.warning("YesCaptcha getTaskResult failed: {}", self.last_error)
                        return None

                    status = data.get("status")
                    if status == "ready":
                        token = data.get("solution", {}).get("token")
                        if token:
                            return token
                        self.last_error = "YesCaptcha returned empty token"
                        logger.warning(self.last_error)
                        return None
                    if status == "processing":
                        if retry_delay > 0:
                            for _ in range(int(retry_delay * 10)):
                                if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                                    return None
                                time.sleep(0.1)
                        continue
                    self.last_error = f"YesCaptcha unexpected status: {status}"
                    logger.warning(self.last_error)
                    if retry_delay > 0:
                        for _ in range(int(retry_delay * 10)):
                            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                                return None
                            time.sleep(0.1)
                    continue

                response = requests.get(
                    f"{self.solver_url}/result",
                    params={"id": task_id},
                    timeout=20,
                )
                response.raise_for_status()
                data = response.json()

                # Solver error -> stop early (avoid polling forever on unsolvable tasks).
                error_id = data.get("errorId")
                if error_id is not None and error_id != 0:
                    self.last_error = str(data.get("errorDescription") or data.get("errorCode") or "solver error")
                    return None

                token = data.get("solution", {}).get("token")
                if token:
                    if token != "CAPTCHA_FAIL":
                        return token
                    self.last_error = "CAPTCHA_FAIL"
                    return None
                if retry_delay > 0:
                    for _ in range(int(retry_delay * 10)):
                        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                            return None
                        time.sleep(0.1)
            except Exception as exc:  # pragma: no cover - network/remote errors
                self.last_error = str(exc)
                logger.debug("Turnstile response error: {}", exc)
                if retry_delay > 0:
                    for _ in range(int(retry_delay * 10)):
                        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                            return None
                        time.sleep(0.1)

        return None

"""Auto registration manager."""
from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.core.config import get_config
from app.core.logger import logger
from app.services.token.manager import get_token_manager
from app.services.register.runner import RegisterRunner
from app.services.register.solver import SolverConfig, TurnstileSolverProcess


@dataclass
class RegisterJob:
    job_id: str
    total: int
    pool: str
    register_threads: int = 10
    status: str = "starting"
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    completed: int = 0
    added: int = 0
    errors: int = 0
    error: Optional[str] = None
    last_error: Optional[str] = None
    tokens: List[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def record_success(self, token: str) -> None:
        with self._lock:
            self.completed += 1
            self.tokens.append(token)

    def record_added(self) -> None:
        with self._lock:
            self.added += 1

    def record_error(self, message: str) -> None:
        message = (message or "").strip()
        if len(message) > 500:
            message = message[:500] + "..."
        with self._lock:
            self.errors += 1
            if message:
                self.last_error = message

    def to_dict(self) -> Dict[str, object]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "pool": self.pool,
                "total": self.total,
                "concurrency": self.register_threads,
                "completed": self.completed,
                "added": self.added,
                "errors": self.errors,
                "error": self.error,
                "last_error": self.last_error,
                "started_at": int(self.started_at * 1000),
                "finished_at": int(self.finished_at * 1000) if self.finished_at else None,
            }


class AutoRegisterManager:
    """Single job manager for auto registration."""

    _instance: Optional["AutoRegisterManager"] = None

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._job: Optional[RegisterJob] = None
        self._task: Optional[asyncio.Task] = None
        self._solver: Optional[TurnstileSolverProcess] = None

    async def start_job(
        self,
        count: int,
        pool: str,
        concurrency: Optional[int] = None,
    ) -> RegisterJob:
        async with self._lock:
            if self._job and self._job.status in {"starting", "running", "stopping"}:
                raise RuntimeError("Auto registration already running")

            default_threads = get_config("register.register_threads", 10)
            try:
                default_threads = max(1, int(default_threads))
            except Exception:
                default_threads = 10

            threads = concurrency if isinstance(concurrency, int) and concurrency > 0 else default_threads

            job = RegisterJob(
                job_id=uuid.uuid4().hex[:8],
                total=count,
                pool=pool,
                register_threads=threads,
            )
            self._job = job
            self._task = asyncio.create_task(self._run_job(job))
            return job

    def get_status(self, job_id: Optional[str] = None) -> Dict[str, object]:
        if not self._job:
            return {"status": "idle"}
        if job_id and self._job.job_id != job_id:
            return {"status": "not_found"}
        return self._job.to_dict()

    async def stop_job(self) -> None:
        """Best-effort stop for the current job (used on shutdown)."""
        async with self._lock:
            job = self._job
            task = self._task
            solver = self._solver

            if not job or job.status not in {"starting", "running"}:
                return
            job.status = "stopping"
            job.stop_event.set()

        # Stop solver first to avoid noisy retries.
        if solver:
            try:
                await asyncio.to_thread(solver.stop)
            except Exception:
                pass

        # Give the runner a short grace period to exit.
        if task:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:
                # Don't block shutdown; the process is exiting anyway.
                pass

    async def _run_job(self, job: RegisterJob) -> None:
        job.status = "starting"

        solver_url = get_config("register.solver_url", "http://127.0.0.1:5072")
        solver_threads = get_config("register.solver_threads", 5)
        try:
            solver_threads = max(1, int(solver_threads))
        except Exception:
            solver_threads = 5

        auto_start_solver = get_config("register.auto_start_solver", True)
        if not isinstance(auto_start_solver, bool):
            auto_start_solver = str(auto_start_solver).lower() in {"1", "true", "yes", "on"}

        # Auto-start only for local solver endpoints.
        try:
            from urllib.parse import urlparse

            host = urlparse(str(solver_url)).hostname or ""
            if host and host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
                auto_start_solver = False
        except Exception:
            pass

        solver_debug = get_config("register.solver_debug", False)
        if not isinstance(solver_debug, bool):
            solver_debug = str(solver_debug).lower() in {"1", "true", "yes", "on"}

        browser_type = str(get_config("register.solver_browser_type", "chromium") or "chromium").strip().lower()
        if browser_type not in {"chromium", "chrome", "msedge", "camoufox"}:
            browser_type = "chromium"

        solver_cfg = SolverConfig(
            url=str(solver_url or "http://127.0.0.1:5072"),
            threads=solver_threads,
            browser_type=browser_type,
            debug=solver_debug,
            auto_start=auto_start_solver,
        )
        solver = TurnstileSolverProcess(solver_cfg)
        self._solver = solver

        use_yescaptcha = bool(str(get_config("register.yescaptcha_key", "") or "").strip())
        if use_yescaptcha:
            # When YesCaptcha is configured we don't need a local solver process.
            auto_start_solver = False
            solver.config.auto_start = False

        # Safety limits to avoid endless loops when upstream is broken.
        max_errors = get_config("register.max_errors", 0)
        try:
            max_errors = int(max_errors)
        except Exception:
            max_errors = 0
        if max_errors <= 0:
            # Default: allow retries, but stop instead of looping "forever".
            max_errors = max(30, int(job.total) * 5)

        max_runtime_minutes = get_config("register.max_runtime_minutes", 0)
        try:
            max_runtime_minutes = float(max_runtime_minutes)
        except Exception:
            max_runtime_minutes = 0
        max_runtime_sec = max_runtime_minutes * 60 if max_runtime_minutes and max_runtime_minutes > 0 else 0

        token_queue: queue.Queue[object] = queue.Queue()
        sentinel = object()

        async def _consume_tokens() -> None:
            mgr = await get_token_manager()
            while True:
                item = await asyncio.to_thread(token_queue.get)
                if item is sentinel:
                    break
                token = str(item or "").strip()
                if not token:
                    continue
                try:
                    if await mgr.add(token, pool_name=job.pool):
                        job.record_added()
                except Exception as exc:
                    job.record_error(f"save token failed: {exc}")

        def _on_error(msg: str) -> None:
            job.record_error(msg)
            # Called from worker threads; keep it simple and thread-safe.
            with job._lock:
                if job.status in {"starting", "running"} and job.errors >= max_errors:
                    job.status = "error"
                    job.error = f"Too many failures ({job.errors}/{max_errors}). Check register config/solver."
                    job.stop_event.set()

        async def _watchdog() -> None:
            if not max_runtime_sec:
                return
            while True:
                await asyncio.sleep(1.0)
                if job.stop_event.is_set():
                    return
                if job.status not in {"starting", "running"}:
                    return
                if (time.time() - job.started_at) >= max_runtime_sec:
                    with job._lock:
                        if job.status in {"starting", "running"}:
                            job.status = "error"
                            job.error = f"Timeout after {max_runtime_minutes:g} minutes."
                            job.stop_event.set()
                    return

        try:
            if auto_start_solver:
                try:
                    await asyncio.to_thread(solver.start)
                except Exception as exc:
                    if not use_yescaptcha:
                        raise
                    logger.warning("Solver start failed, continuing with YesCaptcha: {}", exc)

            job.status = "running"
            watchdog_task = asyncio.create_task(_watchdog())
            consumer_task = asyncio.create_task(_consume_tokens())
            runner = RegisterRunner(
                target_count=job.total,
                thread_count=job.register_threads,
                stop_event=job.stop_event,
                on_success=lambda _email, _password, token, _done, _total: (
                    job.record_success(token),
                    token_queue.put(token),
                ),
                on_error=_on_error,
            )

            await asyncio.to_thread(runner.run)

            # Drain token consumer.
            token_queue.put(sentinel)
            await consumer_task
            if job.status == "stopping":
                job.status = "stopped"
            elif job.status != "error":
                # If we returned without reaching the target, treat it as a failure.
                # This makes issues like "TOS/NSFW not enabled" visible to the UI as a failed job.
                if job.completed < job.total:
                    job.status = "error"
                    suffix = f" Last error: {job.last_error}" if job.last_error else ""
                    job.error = f"Registration ended early ({job.completed}/{job.total}).{suffix}".strip()
                else:
                    job.status = "completed"
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)
            logger.exception("Auto registration failed")
        finally:
            job.finished_at = time.time()
            # Ensure consumer exits even on exceptions.
            try:
                token_queue.put(sentinel)
            except Exception:
                pass
            try:
                if "consumer_task" in locals():
                    await asyncio.wait_for(consumer_task, timeout=10)
            except Exception:
                try:
                    consumer_task.cancel()
                except Exception:
                    pass
            try:
                if "watchdog_task" in locals():
                    watchdog_task.cancel()
            except Exception:
                pass
            self._solver = None
            if auto_start_solver:
                try:
                    await asyncio.to_thread(solver.stop)
                except Exception:
                    pass


def get_auto_register_manager() -> AutoRegisterManager:
    if AutoRegisterManager._instance is None:
        AutoRegisterManager._instance = AutoRegisterManager()
    return AutoRegisterManager._instance


__all__ = ["AutoRegisterManager", "get_auto_register_manager"]

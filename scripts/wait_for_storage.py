import os
import socket
import sys
import time
from urllib.parse import urlparse


DEFAULT_PORTS: dict[str, int] = {
    "redis": 6379,
    "mysql": 3306,
    "pgsql": 5432,
}


def _log(msg: str) -> None:
    print(f"[wait_for_storage] {msg}", flush=True)


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def _get_target(storage_type: str, storage_url: str) -> tuple[str, int] | None:
    if not storage_url:
        return None

    parsed = urlparse(storage_url)
    host = parsed.hostname
    if not host:
        return None

    port = parsed.port
    if port is None:
        port = DEFAULT_PORTS.get(storage_type)

    if port is None:
        return None

    return host, int(port)


def main() -> int:
    storage_type = (os.getenv("SERVER_STORAGE_TYPE", "local") or "local").lower().strip()
    if storage_type in {"", "local"}:
        return 0

    storage_url = (os.getenv("SERVER_STORAGE_URL", "") or "").strip()
    target = _get_target(storage_type, storage_url)
    if target is None:
        _log(
            f"skip: unable to parse host/port for SERVER_STORAGE_TYPE={storage_type!r} from SERVER_STORAGE_URL",
        )
        return 1

    host, port = target
    timeout_s = _as_int(os.getenv("STORAGE_WAIT_TIMEOUT"), 60)
    interval_s = max(0.1, float(os.getenv("STORAGE_WAIT_INTERVAL", "0.5") or "0.5"))

    _log(f"waiting for {storage_type} at {host}:{port} (timeout={timeout_s}s)")

    deadline = time.monotonic() + max(1, timeout_s)
    last_log_at = 0.0

    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                _log("ready")
                return 0
        except OSError as e:
            now = time.monotonic()
            if now - last_log_at >= 3:
                _log(f"not ready yet: {e.__class__.__name__}: {e}")
                last_log_at = now
            time.sleep(interval_s)

    _log("timeout: storage is still not reachable")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


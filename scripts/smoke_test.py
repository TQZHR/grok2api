import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def http_get(url: str, *, headers: dict[str, str] | None = None, timeout: float = 5.0) -> tuple[int, bytes]:
    req = Request(url, headers=headers or {}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200)), resp.read()
    except HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return int(getattr(e, "code", 0) or 0), body


def require_ok(name: str, status: int, body: bytes, *, allow: set[int] | None = None) -> None:
    allow = allow or {200}
    if status in allow:
        return
    text = body.decode("utf-8", errors="replace")
    raise SystemExit(f"[smoke] {name} failed: status={status}, body={text[:500]}")


def try_parse_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Grok2API smoke test (local/docker/cloudflare).")
    parser.add_argument(
        "--base-url",
        default=os.getenv("GROK2API_BASE_URL", "http://127.0.0.1:8000"),
        help="Base URL, e.g. http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("GROK2API_API_KEY", ""),
        help="Optional API key (Bearer). Enables /v1/models check.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("GROK2API_SMOKE_TIMEOUT", "5")),
        help="HTTP timeout seconds.",
    )
    args = parser.parse_args()

    base_url = (args.base_url or "").strip().rstrip("/")
    if not base_url:
        print("[smoke] missing --base-url", file=sys.stderr)
        return 2

    timeout = max(0.5, float(args.timeout))

    # 1) /health
    health_url = f"{base_url}/health"
    try:
        status, body = http_get(health_url, timeout=timeout)
    except URLError as e:
        raise SystemExit(f"[smoke] /health network error: {e}") from e
    require_ok("/health", status, body)
    parsed = try_parse_json(body) or {}
    runtime = (parsed.get("runtime") if isinstance(parsed, dict) else None) or "unknown"
    print(f"[smoke] /health OK (runtime={runtime})")

    # 2) /login (admin UI entry)
    login_url = f"{base_url}/login"
    status, body = http_get(login_url, timeout=timeout)
    require_ok("/login", status, body, allow={200, 302, 307, 308})
    print("[smoke] /login OK")

    # 3) /v1/models (requires API key)
    api_key = (args.api_key or "").strip()
    if api_key:
        models_url = f"{base_url}/v1/models"
        status, body = http_get(models_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
        require_ok("/v1/models", status, body)
        print("[smoke] /v1/models OK")
    else:
        print("[smoke] /v1/models skipped (no --api-key)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


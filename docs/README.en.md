# Grok2API

[中文](../readme.md) | **English**

> [!NOTE]
> This project is for learning and research only. You must comply with Grok's Terms of Use and applicable laws. Do not use it for illegal purposes.

Grok2API rebuilt with **FastAPI**, fully aligned with the latest web call format. Supports streaming and non-streaming chat, image generation/editing, deep thinking, token pool concurrency, and automatic load balancing.

<img width="2562" height="1280" alt="image" src="https://github.com/user-attachments/assets/356d772a-65e1-47bd-abc8-c00bb0e2c9cc" />

<br>

## Cloudflare Workers / Pages (Fork Enhancement)

This fork additionally provides a **Cloudflare Workers / Pages** deployment (TypeScript, D1 + KV) for running Grok2API on Cloudflare:

- Deployment guide: `README.cloudflare.md`
- One-click GitHub Actions workflow: `.github/workflows/cloudflare-workers.yml`

## Usage

### How to start

- Local development

```
uv sync

uv run main.py
```

- Deployment

```
git clone https://github.com/TQZHR/grok2api.git

docker compose up -d
```

### Admin panel

URL: `http://<host>:8000/login`  
Default username/password: `admin` / `admin` (config keys `app.admin_username` / `app.app_key`, change it in production).

Pages:
- `http://<host>:8000/admin/token`: Token management (import/export/batch ops/auto register)
- `http://<host>:8000/admin/datacenter`: Data center (metrics + log viewer)
- `http://<host>:8000/admin/config`: Configuration (including auto register settings)
- `http://<host>:8000/admin/cache`: Cache management (local cache + online assets)

### Auto Register (Token -> Add -> Auto Register)

Auto register will:
- Start a local Turnstile Solver first (default 5 threads), then run registration
- Stop the solver automatically when the job finishes
- After a successful sign-up, it will automatically: accept TOS + enable NSFW
  - If TOS/NSFW fails, the registration attempt is marked as failed and the UI will show the reason

Required config keys (Admin -> Config, `register.*`):
- `register.worker_domain` / `register.email_domain` / `register.admin_password`: temp-mail Worker settings
- `register.solver_url` / `register.solver_browser_type` / `register.solver_threads`: local solver settings
- Optional: `register.yescaptcha_key` (when set, YesCaptcha is preferred and local solver is not required)

### Environment variables

| Variable | Description | Default | Example |
| :--- | :--- | :--- | :--- |
| `LOG_LEVEL` | Log level | `INFO` | `DEBUG` |
| `SERVER_HOST` | Bind address | `0.0.0.0` | `0.0.0.0` |
| `SERVER_PORT` | Service port | `8000` | `8000` |
| `SERVER_WORKERS` | Uvicorn worker count | `1` | `2` |
| `SERVER_STORAGE_TYPE` | Storage type (`local`/`redis`/`mysql`/`pgsql`) | `local` | `pgsql` |
| `SERVER_STORAGE_URL` | Storage URL (empty for local) | `""` | `postgresql+asyncpg://user:password@host:5432/db` |

### Usage limits

- Basic account: 80 requests / 20h
- Super account: not tested by the author

### Models

| Model | Cost | Account | Chat | Image | Video |
| :--- | :---: | :--- | :---: | :---: | :---: |
| `grok-3` | 1 | Basic/Super | Yes | Yes | - |
| `grok-3-fast` | 1 | Basic/Super | Yes | Yes | - |
| `grok-4` | 1 | Basic/Super | Yes | Yes | - |
| `grok-4-mini` | 1 | Basic/Super | Yes | Yes | - |
| `grok-4-fast` | 1 | Basic/Super | Yes | Yes | - |
| `grok-4-heavy` | 4 | Super | Yes | Yes | - |
| `grok-4.1` | 1 | Basic/Super | Yes | Yes | - |
| `grok-4.1-thinking` | 4 | Basic/Super | Yes | Yes | - |
| `grok-imagine-1.0` | 4 | Basic/Super | - | Yes | - |
| `grok-imagine-1.0-video` | - | Basic/Super | - | - | Yes |

<br>

## API

### `POST /v1/chat/completions`
> Generic endpoint: chat, image generation, image editing, video generation, video upscaling

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GROK2API_API_KEY" \
  -d '{
    "model": "grok-4",
    "messages": [{"role":"user","content":"Hello"}]
  }'
```

<details>
<summary>Supported request parameters</summary>

<br>

| Field | Type | Description | Allowed values |
| :--- | :--- | :--- | :--- |
| `model` | string | Model ID | - |
| `messages` | array | Message list | `developer`, `system`, `user`, `assistant` |
| `stream` | boolean | Enable streaming | `true`, `false` |
| `thinking` | string | Thinking mode | `enabled`, `disabled`, `null` |
| `video_config` | object | **Video model only** | - |
| └─ `aspect_ratio` | string | Video aspect ratio | `16:9`, `9:16`, `1:1`, `2:3`, `3:2` |
| └─ `video_length` | integer | Video length (seconds) | `5` - `15` |
| └─ `resolution` | string | Resolution | `SD`, `HD` |
| └─ `preset` | string | Style preset | `fun`, `normal`, `spicy` |

Note: any other parameters will be discarded and ignored.

<br>

</details>

### `POST /v1/images/generations`
> Image endpoint: image generation, image editing

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GROK2API_API_KEY" \
  -d '{
    "model": "grok-imagine-1.0",
    "prompt": "A cat floating in space",
    "n": 1
  }'
```

<details>
<summary>Supported request parameters</summary>

<br>

| Field | Type | Description | Allowed values |
| :--- | :--- | :--- | :--- |
| `model` | string | Image model ID | `grok-imagine-1.0` |
| `prompt` | string | Prompt | - |
| `n` | integer | Number of images | `1` - `10` (streaming: `1` or `2` only) |
| `stream` | boolean | Enable streaming | `true`, `false` |

Note: any other parameters will be discarded and ignored.

<br>

</details>

<br>

## Configuration

Config file: `data/config.toml`

> [!NOTE]
> In production or behind a reverse proxy, make sure `app.app_url` is set to the public URL.
> Otherwise file links may be incorrect or return 403.

## Upgrade & Migration

When upgrading from older versions, the service will keep existing local data and migrate legacy files on startup:

- Legacy config: if `data/setting.toml` exists, it will be merged into `data/config.toml` (only fills missing keys or keys still set to defaults).
- Legacy cache dir: old `data/temp/{image,video}` will be migrated to `data/tmp/{image,video}` so unexpired caches are not lost.
- Legacy accounts (best-effort, one-time): after upgrade, existing tokens will automatically run a TOS + NSFW enablement pass once (concurrency 10) to keep old accounts compatible.
- Docker: make sure `./data:/app/data` (and `./logs:/app/logs`) are mounted persistently, otherwise container rebuilds will lose local data.

| Module | Field | Key | Description | Default |
| :--- | :--- | :--- | :--- | :--- |
| **app** | `app_url` | App URL | External access URL for Grok2API (used for file links). | `http://127.0.0.1:8000` |
| | `admin_username` | Admin username | Username for the Grok2API admin panel. | `admin` |
| | `app_key` | Admin password | Password for the Grok2API admin panel. | `admin` |
| | `api_key` | API key | Bearer token required to call Grok2API. | `""` |
| | `image_format` | Image format | Output image format (`url` or `base64`). | `url` |
| | `video_format` | Video format | Output video format (url only). | `url` |
| **grok** | `temporary` | Temporary chat | Enable temporary conversation mode. | `true` |
| | `stream` | Streaming | Enable streaming by default. | `true` |
| | `thinking` | Thinking chain | Enable model thinking output. | `true` |
| | `dynamic_statsig` | Dynamic fingerprint | Enable dynamic Statsig value generation. | `true` |
| | `filter_tags` | Filter tags | Auto-filter special tags in Grok responses. | `["xaiartifact", "xai:tool_usage_card", "grok:render"]` |
| | `video_poster_preview` | Video poster preview | Replace `<video>` tags in responses with a clickable poster preview image. | `false` |
| | `timeout` | Timeout | Timeout for Grok requests (seconds). | `120` |
| | `base_proxy_url` | Base proxy URL | Base service address proxying Grok official site. | `""` |
| | `asset_proxy_url` | Asset proxy URL | Proxy URL for Grok static assets (images/videos). | `""` |
| | `cf_clearance` | CF Clearance | Cloudflare clearance cookie for verification. | `""` |
| | `max_retry` | Max retries | Max retries on Grok request failure. | `3` |
| | `retry_status_codes` | Retry status codes | HTTP status codes that trigger retry. | `[401, 429, 403]` |
| **token** | `auto_refresh` | Auto refresh | Enable automatic token refresh. | `true` |
| | `refresh_interval_hours` | Refresh interval | Token refresh interval (hours). | `8` |
| | `fail_threshold` | Failure threshold | Consecutive failures before a token is disabled. | `5` |
| | `save_delay_ms` | Save delay | Debounced save delay for token changes (ms). | `500` |
| | `reload_interval_sec` | Consistency refresh | Token state refresh interval in multi-worker setups (sec). | `30` |
| **cache** | `enable_auto_clean` | Auto clean | Enable cache auto clean; cleanup when exceeding limit. | `true` |
| | `limit_mb` | Cleanup threshold | Cache size threshold (MB) that triggers cleanup. | `1024` |
| | `keep_base64_cache` | Keep base64 cache | Keep downloaded image/video cache files when returning Base64 (avoid “local cache = 0”). | `true` |
| **performance** | `assets_max_concurrent` | Assets concurrency | Concurrency cap for assets upload/download/list. Recommended 25. | `25` |
| | `media_max_concurrent` | Media concurrency | Concurrency cap for video/media generation. Recommended 50. | `50` |
| | `usage_max_concurrent` | Usage concurrency | Concurrency cap for usage queries. Recommended 25. | `25` |
| | `assets_delete_batch_size` | Asset cleanup batch | Batch concurrency for online asset deletion. Recommended 10. | `10` |
| | `admin_assets_batch_size` | Admin cleanup batch | Batch concurrency for admin asset stats/cleanup. Recommended 10. | `10` |
| **register** | `worker_domain` | Worker domain | Temp-mail Worker domain (without `https://`). | `""` |
| | `email_domain` | Email domain | Temp-mail domain, e.g. `example.com`. | `""` |
| | `admin_password` | Worker admin password | Admin password/key for the temp-mail Worker panel. | `""` |
| | `yescaptcha_key` | YesCaptcha key | Optional. Prefer YesCaptcha when set. | `""` |
| | `solver_url` | Solver URL | Local Turnstile solver URL. | `http://127.0.0.1:5072` |
| | `solver_browser_type` | Solver browser | `chromium` / `chrome` / `msedge` / `camoufox`. | `camoufox` |
| | `solver_threads` | Solver threads | Threads when auto-starting solver. | `5` |
| | `register_threads` | Register concurrency | Registration concurrency. | `10` |
| | `default_count` | Default count | Default register count if not specified in UI. | `100` |
| | `auto_start_solver` | Auto start solver | Auto-start local solver when using localhost endpoint. | `true` |
| | `solver_debug` | Solver debug | Enable solver debug logging. | `false` |
| | `max_errors` | Max errors | Stop the job after this many failures (0 = auto). | `0` |
| | `max_runtime_minutes` | Max runtime | Stop the job after N minutes (0 = unlimited). | `0` |

<br>

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=TQZHR/grok2api&type=Timeline)](https://star-history.com/#TQZHR/grok2api&Timeline)

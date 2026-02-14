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
  - Prerequisite for one-click workflow: repository secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`.

## Usage

### How to start

- Local development

```
uv sync

uv run main.py

# (Optional) Smoke check
python scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

- Deployment

```
git clone https://github.com/TQZHR/grok2api.git

# Enter the project directory
cd grok2api

# Pull and run the prebuilt image (default)
docker compose up -d

# Update to the latest image
docker compose pull
docker compose up -d

# Build from current source and run (optional)
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build

# (Optional) Smoke check
python scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

> If `docker compose up -d` fails with `denied` while pulling: the GHCR image is not publicly pullable (private or requires auth). Run `docker login ghcr.io`, or set `GROK2API_IMAGE` in `.env` to your own public image; alternatively use `--build` to build from source.

> Optional: copy `.env.example` to `.env` to configure port/logging/storage. You can also set `COMPOSE_PROFILES` to enable `redis/pgsql/mysql` with one compose file (see examples in `.env.example`).

> Deployment consistency: Local (FastAPI), Docker, and Cloudflare Workers share the same admin behavior semantics (token filters, API key management, and admin API responses).
> Cloudflare keeps one-click deployment via `.github/workflows/cloudflare-workers.yml` (with the two required secrets configured), and Docker keeps one-command startup via `docker compose up -d`.

### Admin panel

URL: `http://<host>:8000/login`  
Default username/password: `admin` / `admin` (config keys `app.admin_username` / `app.app_key`, change it in production).

Pages:
- `http://<host>:8000/admin/token`: Token management (import/export/batch ops/auto register)
- `http://<host>:8000/admin/keys`: API key management (stats/filter/create/edit/delete)
- `http://<host>:8000/admin/datacenter`: Data center (metrics + log viewer)
- `http://<host>:8000/admin/config`: Configuration (including auto register settings)
- `http://<host>:8000/admin/cache`: Cache management (local cache + online assets)

### Mobile Responsiveness (Site-wide)

- Covered pages: `/login`, `/admin/token`, `/admin/keys`, `/admin/cache`, `/admin/config`, `/admin/datacenter`, `/chat`, `/admin/chat`.
- Admin top navigation now uses a mobile drawer (open/close, click-mask-to-close, auto-close on link click, `Esc` to close).
- Tables keep a horizontal-scroll-first strategy on mobile (no forced card conversion).
- Toast notifications are edge-aware on narrow screens (no fixed minimum width overflow).
- Bottom batch action bars (Token/Cache) switch to full-width bottom cards on mobile to reduce interaction blocking.
- Same behavior across Local FastAPI, Docker, and Cloudflare Workers because they share the same static frontend assets.

### Token Management Enhancements (Filters + State Rules)

- Type filters: `sso`, `supersso` (combinable).
- Status filters: `active`, `invalid`, `exhausted` (combinable, union semantics).
- Includes result count and reset filters.
- Selection/batch operations after filtering are token-key based (not row-index based), preventing accidental operations on hidden rows.
- State classification rules:
  - `invalid`: `status in invalid/expired/disabled`
  - `exhausted`: `status = cooling`, or (`quota_known = true` and `quota <= 0`), or (super token with `heavy_quota_known = true` and `heavy_quota <= 0`)
  - `active`: neither invalid nor exhausted
- Type mapping: `ssoBasic -> sso`, `ssoSuper -> supersso` (API `token_type` values are `sso` / `ssoSuper`).

### API Key Management Enhancements

- New stat cards: total, active, inactive, exhausted today.
- Toolbar supports search (name/key), status filter (all/active/inactive/exhausted), and reset.
- Create/edit modal improvements:
  - Centered floating modal with mask + entrance animation
  - Click mask or press `Esc` to close
  - Responsive modal grid and scroll behavior on mobile
  - Auto-generate key
  - Quick quota presets (recommended/unlimited)
  - Disable submit button while submitting (prevent duplicate submit)
  - Copy key convenience after successful creation
- Better error surface: frontend now prioritizes backend `detail/error/message`.
- Updating a non-existent key returns `404` on both FastAPI and Workers.

### Auto Register (Token -> Add -> Auto Register)

Auto register will:
- Start a local Turnstile Solver first (default 5 threads), then run registration
- Stop the solver automatically when the job finishes
- After a successful sign-up, it will automatically: accept TOS + set BirthDate + enable NSFW
  - If any TOS/BirthDate/NSFW step fails, the registration attempt is marked as failed and the UI will show the reason

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
| `size` | string | Image size / aspect ratio (experimental method) | `1024x1024`, `16:9`, `9:16`, `1:1`, `2:3`, `3:2` |
| `concurrency` | integer | Parallel upstream calls (experimental method) | `1` - `3` (default `1`) |
| `stream` | boolean | Enable streaming | `true`, `false` |
| `response_format` | string | Output format | `url`, `base64`, `b64_json` (defaults to `app.image_format`) |

Notes:
- when `grok.image_generation_method=imagine_ws_experimental`, `stream=true` uses SSE realtime image events (`image_generation.partial_image` then `image_generation.completed`) and keeps SSE semantics even on fallback.
- `size` is normalized to aspect ratios: `16:9`, `9:16`, `1:1`, `2:3`, `3:2`; unsupported values default to `2:3`.
- any other parameters will be discarded and ignored.

<br>

</details>

<br>

### `GET /v1/images/method`
> Get the active image-generation backend mode (used by `/chat` and `/admin/chat` to toggle the experimental waterfall UI).

```bash
curl http://localhost:8000/v1/images/method \
  -H "Authorization: Bearer $GROK2API_API_KEY"
```

Response example:
```json
{ "image_generation_method": "legacy" }
```

`image_generation_method` values:
- `legacy`
- `imagine_ws_experimental`

### `POST /v1/images/edits`
> Image edit endpoint (`multipart/form-data`)

```bash
curl http://localhost:8000/v1/images/edits \
  -H "Authorization: Bearer $GROK2API_API_KEY" \
  -F "model=grok-imagine-1.0-edit" \
  -F "prompt=Add sunglasses to this cat" \
  -F "image=@./cat.png" \
  -F "n=1" \
  -F "response_format=url"
```

<details>
<summary>Supported request parameters</summary>

<br>

| Field | Type | Description | Allowed values |
| :--- | :--- | :--- | :--- |
| `model` | string | Image model ID | `grok-imagine-1.0-edit` |
| `prompt` | string | Edit prompt | - |
| `image` | file[] | Source image(s), up to 16 files | `png`, `jpg`, `jpeg`, `webp` |
| `n` | integer | Number of images | `1` - `10` (streaming: `1` or `2` only) |
| `stream` | boolean | Enable streaming | `true`, `false` |
| `response_format` | string | Output format | `url`, `base64`, `b64_json` (defaults to `app.image_format`) |

Note: `mask` is currently ignored.

<br>

</details>

<br>

### Admin API Compatibility Changes (FastAPI + Workers)

1. `GET /api/v1/admin/tokens` adds fields (additive, legacy-compatible):
   - `token_type`
   - `quota_known`
   - `heavy_quota`
   - `heavy_quota_known`
2. `POST /api/v1/admin/keys/update`:
   - Returns `404` when key does not exist.
3. Quota semantics:
   - `quota_known = false` means quota is unknown (e.g., `remaining_queries = -1`) and should not be treated as exhausted directly.

## Configuration

Config file: `data/config.toml`

> [!NOTE]
> In production or behind a reverse proxy, make sure `app.app_url` is set to the public URL.
> Otherwise file links may be incorrect or return 403.

## Upgrade & Migration

When upgrading from older versions, the service will keep existing local data and migrate legacy files on startup:

- Legacy config: if `data/setting.toml` exists, it will be merged into `data/config.toml` (only fills missing keys or keys still set to defaults).
- Legacy cache dir: old `data/temp/{image,video}` will be migrated to `data/tmp/{image,video}` so unexpired caches are not lost.
- Legacy accounts (best-effort, one-time): after upgrade, existing tokens will automatically run a TOS + BirthDate + NSFW pass once (concurrency 10) to keep old accounts compatible.
- Docker: make sure `./data:/app/data` (and `./logs:/app/logs`) are mounted persistently, otherwise container rebuilds will lose local data.

| Module | Field | Key | Description | Default |
| :--- | :--- | :--- | :--- | :--- |
| **app** | `app_url` | App URL | External access URL for Grok2API (used for file links). | `http://127.0.0.1:8000` |
| | `admin_username` | Admin username | Username for the Grok2API admin panel. | `admin` |
| | `app_key` | Admin password | Password for the Grok2API admin panel. | `admin` |
| | `api_key` | API key | Bearer token required to call Grok2API. | `""` |
| | `image_format` | Image format | Output image format (`url`, `base64`, or `b64_json`). | `url` |
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
| | `image_generation_method` | Image generation method | Image invoke method (`legacy` is stable default; `imagine_ws_experimental` is experimental). | `legacy` |
| |  |  | Backward-compatible aliases (`imagine_ws`, `experimental`, `new`, `new_method`) are automatically normalized to `imagine_ws_experimental`. |  |
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

## Fixes In This Release

- Fixed token page `refreshStatus` relying on global `event`; now passes button reference explicitly.
- Added unified token normalization (`normalizeSsoToken`) to fix `sso=` dedupe/import/batch-selection inconsistencies.
- Fixed API key update to return `404` for non-existent keys instead of false success.
- Improved token/key page error messages by surfacing backend details (`detail/error/message`).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=TQZHR/grok2api&type=Timeline)](https://star-history.com/#TQZHR/grok2api&Timeline)

# Grok2API

本项目为对 [chenyme/grok2api](https://github.com/chenyme/grok2api) 的二次修改与增强。

**中文** | [English](docs/README.en.md)

> [!NOTE]
> 本项目仅供学习与研究，使用者必须在遵循 Grok 的 **使用条款** 以及 **法律法规** 的情况下使用，不得用于非法用途。

基于 **FastAPI** 重构的 Grok2API，全面适配最新 Web 调用格式，支持流/非流式对话、图像生成/编辑、深度思考，号池并发与自动负载均衡一体化。

<img width="1941" height="1403" alt="screenshot" src="docs/assets/screenshot-2026-02-05-064737.png" />

<br>

## Cloudflare Workers / Pages（Fork 增强）

本仓库额外提供 Cloudflare Workers / Pages（TypeScript，D1 + KV）版本，适合在 Cloudflare 上运行与代理出站。

- 部署与配置说明：`README.cloudflare.md`
- 一键部署工作流：`.github/workflows/cloudflare-workers.yml`
  - 一键部署前置条件：仓库需配置 `CLOUDFLARE_API_TOKEN` 与 `CLOUDFLARE_ACCOUNT_ID`。

## 使用说明

### 如何启动

- 本地开发

```
uv sync

uv run main.py

# （可选）启动后自检
python scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

- 项目部署

```
git clone https://github.com/TQZHR/grok2api.git

# 进入项目目录
cd grok2api

# 直接拉取镜像启动（默认）
docker compose up -d

# 更新到最新镜像
docker compose pull
docker compose up -d

# 从当前仓库源码构建并启动（可选）
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build

# （可选）启动后自检
python scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

> 如果拉取镜像时报 `denied`：说明 GHCR 镜像不可匿名拉取（未公开或需要登录）。你可以先执行 `docker login ghcr.io`，或在 `.env` 里设置 `GROK2API_IMAGE` 指向你自己的公开镜像；也可以用上面的 `--build` 从源码构建运行。

> 可选：复制 `.env.example` 为 `.env`，可配置端口/日志/存储等；并可通过 `COMPOSE_PROFILES` 一键启用 `redis/pgsql/mysql`（见 `.env.example` 内示例）。

> 部署一致性说明：本地（FastAPI）/ Docker / Cloudflare Workers 共用同一套管理功能语义（Token 筛选、API Key 管理、后台管理接口语义一致）。
> Cloudflare 可通过 `.github/workflows/cloudflare-workers.yml` 一键部署（需先配置上述两个 Secrets），Docker 仍保持 `docker compose up -d` 一键启动。

### 管理面板

访问地址：`http://<host>:8000/login`

默认账号密码：`admin` / `admin`（对应配置项 `app.admin_username` / `app.app_key`，建议上线后修改）。

常用页面：
- `http://<host>:8000/admin/token`：Token 管理（导入/导出/批量操作/自动注册）
- `http://<host>:8000/admin/keys`：API Key 管理（统计/筛选/新增/编辑/删除）
- `http://<host>:8000/admin/datacenter`：数据中心（常用指标 + 日志查看）
- `http://<host>:8000/admin/config`：配置管理（含自动注册所需配置）
- `http://<host>:8000/admin/cache`：缓存管理（本地缓存 + 在线资产）

### 手机端适配（全站）

- 已覆盖页面：`/login`、`/admin/token`、`/admin/keys`、`/admin/cache`、`/admin/config`、`/admin/datacenter`、`/chat`、`/admin/chat`。
- 后台顶部导航在手机端改为抽屉菜单（支持：打开/关闭、点击遮罩关闭、点击菜单项后自动收起、`Esc` 关闭）。
- 表格在手机端保持“横向滚动优先”，不压缩列结构（Token/API Key/缓存表格行为一致）。
- Toast 在窄屏改为左右边距自适应，不再固定最小宽度导致溢出。
- 底部批量操作条（Token/缓存）在手机端改为全宽底部卡片样式，减少遮挡主操作。
- 三部署一致性：上述适配使用同一套静态资源，在本地 FastAPI / Docker / Cloudflare Workers 下行为一致。

### Token 管理增强（筛选 + 状态判定）

- 支持类型筛选：`sso`、`supersso`（可组合）。
- 支持状态筛选：`活跃`、`失效`、`额度用尽`（可组合，按并集语义）。
- 提供“结果计数”和“清空筛选”。
- 筛选后勾选/全选/批量刷新/批量删除均基于 Token 唯一值，避免过滤后行索引错位导致误操作。
- 状态判定规则：
  - `失效`：`status` 为 `invalid/expired/disabled`
  - `额度用尽`：`status = cooling`，或（`quota_known = true` 且 `quota <= 0`），或（super 且 `heavy_quota_known = true` 且 `heavy_quota <= 0`）
  - `活跃`：非失效且非额度用尽
- 类型映射规则：`ssoBasic -> sso`，`ssoSuper -> supersso`（接口字段 `token_type` 为 `sso` / `ssoSuper`）。

### API Key 管理增强

- 页面新增统计卡片：总数、启用、禁用、今日额度用尽。
- 工具栏支持：名称/Key 搜索、状态筛选（全部/启用/禁用/额度用尽）、重置筛选。
- 新增 API Key 弹窗增强：
  - 居中悬浮弹窗（遮罩层 + 缩放入场动画）
  - 支持点击遮罩关闭、`Esc` 关闭
  - 移动端弹窗内容可滚动且网格布局自适应
  - 自动生成 Key
  - 额度预设（推荐/不限）
  - 提交中禁用按钮，防止重复提交
  - 创建成功后支持一键复制 Key
- 错误提示优化：前端优先展示后端 `detail/error/message`，避免“创建失败/更新失败”无上下文。
- 更新不存在的 Key 会返回 `404`（FastAPI 与 Workers 一致）。

### 自动注册（Token 管理 -> 添加 -> 自动注册）

支持两种方式：
- 直接添加 Token（手动/批量导入）
- 自动注册并自动写入 Token 池

自动注册特性：
- 可设置注册数量（不填默认 `100`）
- 可设置并发（默认 `10`）
- 注册前会自动启动本地 Turnstile Solver（默认 5 线程），注册结束后自动关闭
- 注册成功后会自动执行：同意用户协议（TOS）+ 设置年龄 + 开启 NSFW
  - 若 TOS / 年龄 / NSFW 任一步骤失败，会判定该次注册失败并在前端显示错误原因

自动注册前置配置（在「配置管理」-> `register.*`）：
- `register.worker_domain` / `register.email_domain` / `register.admin_password`：临时邮箱 Worker 配置
- `register.solver_url` / `register.solver_browser_type` / `register.solver_threads`：本地 Turnstile Solver 配置
- 可选：`register.yescaptcha_key`（配置后优先走 YesCaptcha，无需本地 solver）

升级兼容：
- 本地部署升级后会自动对「旧 Token」做一次 TOS + 设置年龄 + NSFW（并发 10，best-effort，仅执行一次，避免重复刷）。

### 环境变量

> 配置 `.env` 文件

| 变量名                  | 说明                                                | 默认值      | 示例                                                |
| :---------------------- | :-------------------------------------------------- | :---------- | :-------------------------------------------------- |
| `LOG_LEVEL`           | 日志级别                                            | `INFO`    | `DEBUG`                                           |
| `SERVER_HOST`         | 服务监听地址                                        | `0.0.0.0` | `0.0.0.0`                                         |
| `SERVER_PORT`         | 服务端口                                            | `8000`    | `8000`                                            |
| `SERVER_WORKERS`      | Uvicorn worker 数量                                 | `1`       | `2`                                               |
| `SERVER_STORAGE_TYPE` | 存储类型（`local`/`redis`/`mysql`/`pgsql`） | `local`   | `pgsql`                                           |
| `SERVER_STORAGE_URL`  | 存储连接串（local 时可为空）                        | `""`      | `postgresql+asyncpg://user:password@host:5432/db` |

### 配置文件与升级迁移

- 配置文件：`data/config.toml`（首次启动会基于 `config.defaults.toml` 自动生成；管理面板也可直接修改）
- Token 数据：`data/token.json`
- 升级时自动兼容迁移（本地/Docker）：
  - 旧版配置：检测到 `data/setting.toml` 时，会按“缺失字段/仍为默认值”的策略合并到新配置
  - 旧版缓存目录：`data/temp/{image,video}` -> `data/tmp/{image,video}`
  - 旧账号一次性修复（best-effort）：升级后会对现有 Token 自动执行一次「同意用户协议 + 设置年龄 + 开启 NSFW」（并发 10）


### 可用次数

- Basic 账号：80 次 / 20h
- Super 账号：无账号，作者未测试

### 可用模型

| 模型名                     | 计次 | 可用账号    | 对话功能 | 图像功能 | 视频功能 |
| :------------------------- | :--: | :---------- | :------: | :------: | :------: |
| `grok-3`                 |  1  | Basic/Super |   支持   |   支持   |    -    |
| `grok-3-fast`            |  1  | Basic/Super |   支持   |   支持   |    -    |
| `grok-4`                 |  1  | Basic/Super |   支持   |   支持   |    -    |
| `grok-4-mini`            |  1  | Basic/Super |   支持   |   支持   |    -    |
| `grok-4-fast`            |  1  | Basic/Super |   支持   |   支持   |    -    |
| `grok-4-heavy`           |  4  | Super       |   支持   |   支持   |    -    |
| `grok-4.1`               |  1  | Basic/Super |   支持   |   支持   |    -    |
| `grok-4.1-thinking`      |  4  | Basic/Super |   支持   |   支持   |    -    |
| `grok-imagine-1.0`       |  4  | Basic/Super |    -    |   支持   |    -    |
| `grok-imagine-1.0-edit`  |  4  | Basic/Super |    -    |   支持   |    -    |
| `grok-imagine-1.0-video` |  -  | Basic/Super |    -    |    -    |   支持   |

<br>

## 接口说明

### `POST /v1/chat/completions`

> 通用接口，支持对话聊天、图像生成、图像编辑、视频生成、视频超分

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GROK2API_API_KEY" \
  -d '{
    "model": "grok-4",
    "messages": [{"role":"user","content":"你好"}]
  }'
```

<details>
<summary>支持的请求参数</summary>

<br>

| 字段                 | 类型    | 说明                           | 可用参数                                           |
| :------------------- | :------ | :----------------------------- | :------------------------------------------------- |
| `model`            | string  | 模型名称                       | -                                                  |
| `messages`         | array   | 消息列表                       | `developer`, `system`, `user`, `assistant` |
| `stream`           | boolean | 是否开启流式输出               | `true`, `false`                                |
| `thinking`         | string  | 思维链模式                     | `enabled`, `disabled`, `null`                |
| `video_config`     | object  | **视频模型专用配置对象** | -                                                  |
| └─`aspect_ratio` | string  | 视频宽高比                     | `16:9`, `9:16`, `1:1`, `2:3`, `3:2`      |
| └─`video_length` | integer | 视频时长 (秒)                  | `5` - `15`                                     |
| └─`resolution`   | string  | 分辨率                         | `SD`, `HD`                                     |
| └─`preset`       | string  | 风格预设                       | `fun`, `normal`, `spicy`                     |

注：除上述外的其他参数将自动丢弃并忽略

<br>

</details>

### `POST /v1/images/generations`

> 图像生成接口

```bash
curl http://localhost:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GROK2API_API_KEY" \
  -d '{
    "model": "grok-imagine-1.0",
    "prompt": "一只在太空漂浮的猫",
    "n": 1
  }'
```

<details>
<summary>支持的请求参数</summary>

<br>

| 字段 | 类型 | 说明 | 可用参数 |
| :--- | :--- | :--- | :--- |
| `model` | string | 图像模型名 | `grok-imagine-1.0` |
| `prompt` | string | 图像描述提示词 | - |
| `n` | integer | 生成数量 | `1` - `10`（流式仅 `1` 或 `2`） |
| `stream` | boolean | 是否开启流式输出 | `true`, `false` |
| `size` | string | 图片尺寸/比例 | `1024x1024`、`16:9`、`9:16`、`1:1`、`2:3`、`3:2` |
| `concurrency` | integer | 新方式并发数 | `1` - `3`（仅新生图方式生效） |
| `response_format` | string | 图片返回格式 | `url`, `base64`, `b64_json`（默认跟随 `app.image_format`） |

注：
- `grok.image_generation_method=imagine_ws_experimental` 支持 `single`（单次）与 `continuous`（持续）两种模式。
- `size` 在新方式下会映射为比例：`1024x576/1280x720/1536x864 -> 16:9`，`576x1024/720x1280/864x1536 -> 9:16`，`1024x1024/512x512 -> 1:1`，`1024x1536/512x768/768x1024 -> 2:3`，`1536x1024/768x512/1024x768 -> 3:2`；其他值默认 `2:3`。
- 除上述外的其他参数将自动丢弃并忽略。

<br>

</details>

<br>

### `GET /v1/images/method`

> 返回当前生图后端方式（`/chat` 与 `/admin/chat` 用于判断是否启用“新生图瀑布流 + 宽高比 + 并发”）

```bash
curl http://localhost:8000/v1/images/method \
  -H "Authorization: Bearer $GROK2API_API_KEY"
```

返回示例：
```json
{ "image_generation_method": "legacy" }
```

- 可选值：`legacy`、`imagine_ws_experimental`
- Cloudflare / Docker / 本地 三种部署均保持同一接口语义

<br>

#### `imagine_ws_experimental` (`/chat` + `/admin/chat`)

- In experimental mode, the image panel is replaced and supports two run modes: `single` and `continuous`.
- `single` keeps using `POST /v1/images/generations` and remains response-compatible.
- `continuous` uses WebSocket: `/api/v1/admin/imagine/ws?api_key=<API_KEY>`.
- WS commands: `start` / `stop` / `ping`.
- WS events: `status` / `image` / `error` / `pong`.
- Continuous payload includes `b64_json`, `sequence`, `elapsed_ms`, `aspect_ratio`, `run_id`.

### `POST /v1/images/edits`

> 图像编辑接口（`multipart/form-data`）

```bash
curl http://localhost:8000/v1/images/edits \
  -H "Authorization: Bearer $GROK2API_API_KEY" \
  -F "model=grok-imagine-1.0-edit" \
  -F "prompt=给这只猫加一副太阳镜" \
  -F "image=@./cat.png" \
  -F "n=1" \
  -F "response_format=url"
```

<details>
<summary>支持的请求参数</summary>

<br>

| 字段 | 类型 | 说明 | 可用参数 |
| :--- | :--- | :--- | :--- |
| `model` | string | 图像模型名 | `grok-imagine-1.0-edit` |
| `prompt` | string | 编辑提示词 | - |
| `image` | file[] | 待编辑图片（最多 16 张） | `png`, `jpg`, `jpeg`, `webp` |
| `n` | integer | 生成数量 | `1` - `10`（流式仅 `1` 或 `2`） |
| `stream` | boolean | 是否开启流式输出 | `true`, `false` |
| `response_format` | string | 图片返回格式 | `url`, `base64`, `b64_json`（默认跟随 `app.image_format`） |

注：`mask` 参数当前未实现，会被忽略。

<br>

</details>

<br>

### 后台管理 API 兼容变更（FastAPI + Workers）

1. `GET /api/v1/admin/tokens`（增量兼容，保留旧字段）新增：
   - `token_type`
   - `quota_known`
   - `heavy_quota`
   - `heavy_quota_known`
2. `POST /api/v1/admin/keys/update`：
   - 当 key 不存在时返回 `404`（此前部分实现可能返回成功）。
3. 额度语义补充：
   - `quota_known = false` 表示额度未知（例如 `remaining_queries = -1` 场景），不应直接判定为“额度用尽”。

## 参数配置

配置文件：`data/config.toml`

> [!NOTE]
> 生产环境或反向代理部署时，请确保 `app.app_url` 配置为对外可访问的完整 URL，
> 否则可能出现文件访问链接不正确或 403 等问题。

### 升级迁移（不丢数据）

当你从旧版本升级到当前版本时，程序会在启动时自动兼容并读取旧数据：

- 旧配置：若存在 `data/setting.toml`，会自动迁移/合并到 `data/config.toml`（仅覆盖“缺失项”或“仍为默认值”的字段）。
- 旧缓存目录：旧版 `data/temp/{image,video}` 会自动迁移到新版 `data/tmp/{image,video}`，未到清理时间的缓存文件不会丢失。
- Docker 部署：务必持久化挂载 `./data:/app/data`（以及 `./logs:/app/logs`），否则容器更新/重建会丢失本地数据。

| 模块                  | 字段                         | 配置名       | 说明                                                 | 默认值                                                    |
| :-------------------- | :--------------------------- | :----------- | :--------------------------------------------------- | :-------------------------------------------------------- |
| **app**         | `app_url`                  | 应用地址     | 当前 Grok2API 服务的外部访问 URL，用于文件链接访问。 | `http://127.0.0.1:8000`                                 |
|                       | `admin_username`           | 后台账号     | 登录 Grok2API 服务管理后台的用户名。                 | `admin`                                                 |
|                       | `app_key`                  | 后台密码     | 登录 Grok2API 服务管理后台的密码，请妥善保管。       | `admin`                                                 |
|                       | `api_key`                  | API 密钥     | 调用 Grok2API 服务所需的 Bearer Token，请妥善保管。  | `""`                                                    |
|                       | `image_format`             | 图片格式     | 生成的图片格式（url / base64 / b64_json）。          | `url`                                                   |
|                       | `video_format`             | 视频格式     | 生成的视频格式（仅支持 url）。                       | `url`                                                   |
| **grok**        | `temporary`                | 临时对话     | 是否启用临时对话模式。                               | `true`                                                  |
|                       | `stream`                   | 流式响应     | 是否默认启用流式输出。                               | `true`                                                  |
|                       | `thinking`                 | 思维链       | 是否启用模型思维链输出。                             | `true`                                                  |
|                       | `dynamic_statsig`          | 动态指纹     | 是否启用动态生成 Statsig 值。                        | `true`                                                  |
|                       | `filter_tags`              | 过滤标签     | 自动过滤 Grok 响应中的特殊标签。                     | `["xaiartifact", "xai:tool_usage_card", "grok:render"]` |
|                       | `video_poster_preview`     | 视频海报预览 | 将返回内容中的 `<video>` 标签替换为可点击的 Poster 预览图。 | `false`                                                 |
|                       | `timeout`                  | 超时时间     | 请求 Grok 服务的超时时间（秒）。                     | `120`                                                   |
|                       | `base_proxy_url`           | 基础代理 URL | 代理请求到 Grok 官网的基础服务地址。                 | `""`                                                    |
|                       | `asset_proxy_url`          | 资源代理 URL | 代理请求到 Grok 官网的静态资源（图片/视频）地址。    | `""`                                                    |
|                       | `cf_clearance`             | CF Clearance | Cloudflare 验证 Cookie，用于验证 Cloudflare 的验证。 | `""`                                                    |
|                       | `max_retry`                | 最大重试     | 请求 Grok 服务失败时的最大重试次数。                 | `3`                                                     |
|                       | `retry_status_codes`       | 重试状态码   | 触发重试的 HTTP 状态码列表。                         | `[401, 429, 403]`                                       |
|                       | `image_generation_method`  | 生图调用方式 | 生图调用方式（`legacy` 旧方法；`imagine_ws_experimental` 新方法，实验性）。 | `legacy`                                                |
| **token**       | `auto_refresh`             | 自动刷新     | 是否开启 Token 自动刷新机制。                        | `true`                                                  |
|                       | `refresh_interval_hours`   | 刷新间隔     | Token 刷新的时间间隔（小时）。                       | `8`                                                     |
|                       | `fail_threshold`           | 失败阈值     | 单个 Token 连续失败多少次后被标记为不可用。          | `5`                                                     |
|                       | `save_delay_ms`            | 保存延迟     | Token 变更合并写入的延迟（毫秒）。                   | `500`                                                   |
|                       | `reload_interval_sec`      | 一致性刷新   | 多 worker 场景下 Token 状态刷新间隔（秒）。          | `30`                                                    |
|                       | `nsfw_refresh_concurrency` | NSFW 刷新并发 | 同意协议/年龄/NSFW 刷新的默认并发数。                | `10`                                                    |
|                       | `nsfw_refresh_retries`     | NSFW 刷新重试 | 刷新失败后的额外重试次数（不含首次）。               | `3`                                                     |
| **cache**       | `enable_auto_clean`        | 自动清理     | 是否启用缓存自动清理，开启后按上限自动回收。         | `true`                                                  |
|                       | `limit_mb`                 | 清理阈值     | 缓存大小阈值（MB），超过阈值会触发清理。             | `1024`                                                  |
| **performance** | `assets_max_concurrent`    | 资产并发上限 | 资源上传/下载/列表的并发上限。推荐 25。              | `25`                                                    |
|                       | `media_max_concurrent`     | 媒体并发上限 | 视频/媒体生成请求的并发上限。推荐 50。               | `50`                                                    |
|                       | `usage_max_concurrent`     | 用量并发上限 | 用量查询请求的并发上限。推荐 25。                    | `25`                                                    |
|                       | `assets_delete_batch_size` | 资产清理批量 | 在线资产删除单批并发数量。推荐 10。                  | `10`                                                    |
|                       | `admin_assets_batch_size`  | 管理端批量   | 管理端在线资产统计/清理批量并发数量。推荐 10。       | `10`                                                    |

<br>

## 本次修复

- 修复 Token 页 `refreshStatus` 依赖全局 `event` 的问题，改为显式传入按钮引用，避免不同运行环境下按钮状态异常。
- 新增 Token 统一归一化（`normalizeSsoToken`），修复 `sso=` 前缀导致的去重、导入、批量选择不一致问题。
- 修复 API Key 更新接口“key 不存在仍返回成功”问题，统一为 `404`。
- 优化 Token/API Key 页面错误提示，优先展示后端具体错误（`detail/error/message`）。

## 本次更新补充（本地/Docker）

- 新增：导入/手动添加/外部写入新增 Token 后，会在后台自动执行 `同意协议 + 设置年龄 + 开启 NSFW`。
- 新增：Token 管理页增加「一键刷新 NSFW」按钮，默认对全部 Token 执行上述流程。
- 新增：批量刷新默认并发 `10`，失败后额外重试 `3` 次；重试耗尽自动标记为失效。
- 新增配置：
  - `token.nsfw_refresh_concurrency`（默认 `10`）
  - `token.nsfw_refresh_retries`（默认 `3`）
- 说明：该功能仅在 `python-fastapi`（本地/Docker）开放；`cloudflare-workers` 侧不展示该按钮。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=TQZHR/grok2api&type=Timeline)](https://star-history.com/#TQZHR/grok2api&Timeline)

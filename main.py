"""
Grok2API 应用入口

FastAPI 应用初始化和路由注册
"""

from contextlib import asynccontextmanager
import asyncio
import os
import platform
import sys
from pathlib import Path

from dotenv import load_dotenv

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(env_file)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends

from app.core.auth import verify_api_key
from app.core.config import get_config
from app.core.logger import logger, setup_logging
from app.core.exceptions import register_exception_handlers
from app.core.response_middleware import ResponseLoggerMiddleware
from app.api.v1.chat import router as chat_router
from app.api.v1.image import router as image_router
from app.api.v1.files import router as files_router
from app.api.v1.models import router as models_router
from app.services.token import get_scheduler


# 初始化日志
setup_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_console=False,
    file_logging=True,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""

    # 0. 兼容迁移：保留旧版 data 目录中的配置/缓存等数据
    from app.core.legacy_migration import migrate_legacy_cache_dirs, migrate_legacy_account_settings

    await asyncio.to_thread(migrate_legacy_cache_dirs)

    # 1. 加载配置（内部会自动合并 defaults + 兼容 setting.toml）
    from app.core.config import config

    await config.load()

    # 1.1 Old account post-migration settings (TOS + NSFW), best-effort
    async def _run_legacy_account_migration():
        try:
            await migrate_legacy_account_settings(concurrency=10)
        except Exception as e:
            logger.warning(f"Legacy account migration failed: {e}")

    asyncio.create_task(_run_legacy_account_migration())

    # 2. 启动服务显示
    logger.info("Starting Grok2API...")
    logger.info(f"Platform: {platform.system()} {platform.release()}")
    logger.info(f"Python: {sys.version.split()[0]}")

    # 3. 启动 Token 刷新调度器
    refresh_enabled = get_config("token.auto_refresh", True)
    if refresh_enabled:
        interval = get_config("token.refresh_interval_hours", 8)
        scheduler = get_scheduler(interval)
        scheduler.start()

    logger.info("Application startup complete.")
    yield

    # 关闭
    logger.info("Shutting down Grok2API...")

    # Best-effort: stop auto-register to avoid blocking shutdown on background threads.
    try:
        from app.services.register import get_auto_register_manager

        await get_auto_register_manager().stop_job()
    except Exception:
        pass

    from app.core.storage import StorageFactory

    if StorageFactory._instance:
        await StorageFactory._instance.close()

    if refresh_enabled:
        scheduler = get_scheduler()
        scheduler.stop()


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title="Grok2API",
        lifespan=lifespan,
    )

    # CORS 配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 请求日志和 ID 中间件
    app.add_middleware(ResponseLoggerMiddleware)

    # 注册异常处理器
    register_exception_handlers(app)

    # 注册路由
    app.include_router(chat_router, prefix="/v1", dependencies=[Depends(verify_api_key)])
    app.include_router(image_router, prefix="/v1", dependencies=[Depends(verify_api_key)])
    app.include_router(models_router, prefix="/v1", dependencies=[Depends(verify_api_key)])
    app.include_router(files_router, prefix="/v1/files")

    # 静态文件服务
    #
    # NOTE: Starlette/StaticFiles serves JS as `application/javascript` without a charset.
    # Some browsers/OS locales may then mis-decode UTF-8 and display `????` for Chinese text.
    # Force `charset=utf-8` for JS to avoid mojibake across environments (local/docker).
    from fastapi.staticfiles import StaticFiles

    static_dir = Path(__file__).parent / "app" / "static"
    if static_dir.exists():
        class _UTF8StaticFiles(StaticFiles):
            async def get_response(self, path: str, scope):  # type: ignore[override]
                resp = await super().get_response(path, scope)

                # Starlette uses `mimetypes` which may vary across OS/distros.
                # Ensure UTF-8 decoding for text-like assets to avoid mojibake (`????`) on some locales.
                ctype = (resp.headers.get("content-type", "") or "").strip()
                ctype_l = ctype.lower()
                if "charset=" in ctype_l:
                    return resp

                base = ctype.split(";", 1)[0].strip().lower()
                is_text = base.startswith("text/")
                is_js = base in ("application/javascript", "text/javascript")
                is_json = base == "application/json"
                is_css = base == "text/css"

                # Some servers might respond with empty content-type for 304 etc; fall back by extension.
                if not base:
                    ext = Path(path).suffix.lower()
                    if ext in (".js", ".mjs"):
                        resp.headers["content-type"] = "application/javascript; charset=utf-8"
                    elif ext == ".css":
                        resp.headers["content-type"] = "text/css; charset=utf-8"
                    elif ext in (".html", ".htm"):
                        resp.headers["content-type"] = "text/html; charset=utf-8"
                    return resp

                if is_text or is_js or is_json or is_css:
                    resp.headers["content-type"] = f"{base}; charset=utf-8"
                return resp

        app.mount("/static", _UTF8StaticFiles(directory=static_dir), name="static")

    # 注册管理路由
    from app.api.v1.admin import router as admin_router

    app.include_router(admin_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    workers = int(os.getenv("SERVER_WORKERS", "1"))

    # 平台检查
    is_windows = platform.system() == "Windows"

    # 自动降级
    if is_windows and workers > 1:
        logger.warning(
            f"Windows platform detected. Multiple workers ({workers}) is not supported. "
            "Using single worker instead.",
        )
        workers = 1

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        workers=workers,
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )

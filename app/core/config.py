"""
配置管理

- config.toml: 运行时配置
- config.defaults.toml: 默认配置基线
"""

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict
import tomllib

from app.core.logger import logger

DEFAULT_CONFIG_FILE = Path(__file__).parent.parent.parent / "config.defaults.toml"
LEGACY_CONFIG_FILE = Path(__file__).parent.parent.parent / "data" / "setting.toml"


def _as_str(v: Any) -> str:
    if isinstance(v, str):
        return v
    return ""


def _as_int(v: Any) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def _as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    return None


def _split_csv_tags(v: Any) -> list[str] | None:
    if not isinstance(v, str):
        return None
    parts = [x.strip() for x in v.split(",")]
    tags = [x for x in parts if x]
    return tags or None


def _legacy_setting_to_config(legacy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate legacy `data/setting.toml` format (grok/global) to the new config schema.

    Best-effort mapping only for stable fields. It does not delete or rename the legacy file.
    """

    grok = legacy.get("grok") if isinstance(legacy.get("grok"), dict) else {}
    global_ = legacy.get("global") if isinstance(legacy.get("global"), dict) else {}

    out: Dict[str, Any] = {}

    # === app ===
    app_url = _as_str(global_.get("base_url")).strip()
    admin_username = _as_str(global_.get("admin_username")).strip()
    app_key = _as_str(global_.get("admin_password")).strip()
    api_key = _as_str(grok.get("api_key")).strip()
    image_format = _as_str(global_.get("image_mode")).strip()

    if app_url or admin_username or app_key or api_key or image_format:
        out["app"] = {}
        if app_url:
            out["app"]["app_url"] = app_url
        if admin_username:
            out["app"]["admin_username"] = admin_username
        if app_key:
            out["app"]["app_key"] = app_key
        if api_key:
            out["app"]["api_key"] = api_key
        if image_format:
            out["app"]["image_format"] = image_format

    # === grok ===
    base_proxy_url = _as_str(grok.get("proxy_url")).strip()
    asset_proxy_url = _as_str(grok.get("cache_proxy_url")).strip()
    cf_clearance = _as_str(grok.get("cf_clearance")).strip()

    temporary = _as_bool(grok.get("temporary"))
    thinking = _as_bool(grok.get("show_thinking"))
    dynamic_statsig = _as_bool(grok.get("dynamic_statsig"))
    filter_tags = _split_csv_tags(grok.get("filtered_tags"))

    retry_status_codes = grok.get("retry_status_codes")

    timeout = None
    total_timeout = _as_int(grok.get("stream_total_timeout"))
    if total_timeout and total_timeout > 0:
        timeout = total_timeout
    else:
        chunk_timeout = _as_int(grok.get("stream_chunk_timeout"))
        if chunk_timeout and chunk_timeout > 0:
            timeout = chunk_timeout

    if (
        base_proxy_url
        or asset_proxy_url
        or cf_clearance
        or temporary is not None
        or thinking is not None
        or dynamic_statsig is not None
        or filter_tags is not None
        or timeout is not None
        or isinstance(retry_status_codes, list)
    ):
        out["grok"] = {}
        if base_proxy_url:
            out["grok"]["base_proxy_url"] = base_proxy_url
        if asset_proxy_url:
            out["grok"]["asset_proxy_url"] = asset_proxy_url
        if cf_clearance:
            out["grok"]["cf_clearance"] = cf_clearance
        if temporary is not None:
            out["grok"]["temporary"] = temporary
        if thinking is not None:
            out["grok"]["thinking"] = thinking
        if dynamic_statsig is not None:
            out["grok"]["dynamic_statsig"] = dynamic_statsig
        if filter_tags is not None:
            out["grok"]["filter_tags"] = filter_tags
        if timeout is not None:
            out["grok"]["timeout"] = timeout
        if isinstance(retry_status_codes, list) and retry_status_codes:
            out["grok"]["retry_status_codes"] = retry_status_codes

    # === cache ===
    # Legacy had separate limits; new uses a single total limit_mb.
    image_mb = _as_int(global_.get("image_cache_max_size_mb")) or 0
    video_mb = _as_int(global_.get("video_cache_max_size_mb")) or 0
    if image_mb > 0 or video_mb > 0:
        out["cache"] = {"limit_mb": max(1, image_mb + video_mb)}

    return out


def _apply_legacy_config(
    config_data: Dict[str, Any],
    legacy_cfg: Dict[str, Any],
    defaults: Dict[str, Any],
) -> bool:
    """
    Merge legacy settings into current config:
    - fill missing keys
    - override keys that are still default values
    """

    changed = False
    for section, items in legacy_cfg.items():
        if not isinstance(items, dict):
            continue

        current_section = config_data.get(section)
        if not isinstance(current_section, dict):
            current_section = {}
            config_data[section] = current_section
            changed = True

        default_section = defaults.get(section) if isinstance(defaults.get(section), dict) else {}

        for key, val in items.items():
            if val is None:
                continue
            if key not in current_section:
                current_section[key] = val
                changed = True
                continue

            default_val = default_section.get(key) if isinstance(default_section, dict) else None
            current_val = current_section.get(key)

            # NOTE: The admin panel password default used to be `grok2api` in older versions.
            # Treat it as "still default" so legacy `data/setting.toml` can override it during migration.
            is_effective_default = current_val == default_val
            if section == "app" and key == "app_key" and current_val == "grok2api":
                is_effective_default = True

            if is_effective_default and val != default_val:
                current_section[key] = val
                changed = True

    return changed


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并字典：override 覆盖 base。"""
    if not isinstance(base, dict):
        return deepcopy(override) if isinstance(override, dict) else deepcopy(base)

    result = deepcopy(base)
    if not isinstance(override, dict):
        return result

    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_defaults() -> Dict[str, Any]:
    """加载默认配置文件"""
    if not DEFAULT_CONFIG_FILE.exists():
        return {}
    try:
        with DEFAULT_CONFIG_FILE.open("rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.warning(f"Failed to load defaults from {DEFAULT_CONFIG_FILE}: {e}")
        return {}


class Config:
    """配置管理器"""

    _instance = None
    _config = {}

    def __init__(self):
        self._config = {}
        self._defaults = {}
        self._defaults_loaded = False

    def _ensure_defaults(self):
        if self._defaults_loaded:
            return
        self._defaults = _load_defaults()
        self._defaults_loaded = True

    async def load(self):
        """显式加载配置"""
        try:
            from app.core.storage import get_storage, LocalStorage

            self._ensure_defaults()

            storage = get_storage()
            config_data = await storage.load_config()
            from_remote = True

            # 从本地 data/config.toml 初始化后端
            if config_data is None:
                local_storage = LocalStorage()
                from_remote = False
                try:
                    config_data = await local_storage.load_config()
                except Exception as e:
                    logger.info(f"Failed to auto-init config from local: {e}")
                    config_data = {}

            config_data = config_data or {}
            before_legacy = deepcopy(config_data)

            # Legacy migration: data/setting.toml -> config schema
            if LEGACY_CONFIG_FILE.exists():
                try:
                    with LEGACY_CONFIG_FILE.open("rb") as f:
                        legacy_raw = tomllib.load(f) or {}
                    legacy_cfg = _legacy_setting_to_config(legacy_raw)
                    if legacy_cfg and _apply_legacy_config(config_data, legacy_cfg, self._defaults):
                        logger.info(
                            "Detected legacy data/setting.toml, migrated into config (missing/default keys)."
                        )
                except Exception as e:
                    logger.warning(f"Failed to migrate legacy config from {LEGACY_CONFIG_FILE}: {e}")

            merged = _deep_merge(self._defaults, config_data)

            # 自动回填缺失配置到存储
            should_persist = (not from_remote) or (merged != before_legacy)
            if should_persist:
                async with storage.acquire_lock("config_save", timeout=10):
                    await storage.save_config(merged)
                if not from_remote:
                    logger.info(
                        f"Initialized remote storage ({storage.__class__.__name__}) with config baseline."
                    )

            self._config = merged
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            self._config = {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值

        Args:
            key: 配置键，格式 "section.key"
            default: 默认值
        """
        if "." in key:
            try:
                section, attr = key.split(".", 1)
                return self._config.get(section, {}).get(attr, default)
            except (ValueError, AttributeError):
                return default

        return self._config.get(key, default)

    async def update(self, new_config: dict):
        """更新配置"""
        from app.core.storage import get_storage

        storage = get_storage()
        async with storage.acquire_lock("config_save", timeout=10):
            self._ensure_defaults()
            base = _deep_merge(self._defaults, self._config or {})
            merged = _deep_merge(base, new_config or {})
            await storage.save_config(merged)
            self._config = merged


# 全局配置实例
config = Config()


def get_config(key: str, default: Any = None) -> Any:
    """获取配置"""
    return config.get(key, default)


__all__ = ["Config", "config", "get_config"]

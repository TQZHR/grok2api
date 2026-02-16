"""API Key 管理器 - 多用户密钥管理"""

import orjson
import time
import os
import secrets
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any, Tuple
from pathlib import Path

from app.core.logger import logger
from app.core.config import get_config


class ApiKeyManager:
    """API Key 管理服务"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
            
        self.file_path = Path(__file__).parents[2] / "data" / "api_keys.json"
        self.usage_path = Path(__file__).parents[2] / "data" / "api_key_usage.json"
        self._keys: List[Dict] = []
        self._lock = asyncio.Lock()
        self._loaded = False

        self._usage: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._usage_lock = asyncio.Lock()
        self._usage_loaded = False
        
        self._initialized = True
        logger.debug(f"[ApiKey] 初始化完成: {self.file_path}")

    async def init(self):
        """初始化加载数据"""
        if not self._loaded:
            await self._load_data()
        if not self._usage_loaded:
            await self._load_usage_data()

    async def _load_data(self):
        """加载 API Keys"""
        if self._loaded:
            return

        if not self.file_path.exists():
            self._keys = []
            self._loaded = True
            return

        try:
            async with self._lock:
                content = await asyncio.to_thread(self.file_path.read_bytes)
                if content:
                    data = orjson.loads(content)
                    if isinstance(data, list):
                        out: List[Dict[str, Any]] = []
                        for item in data:
                            if not isinstance(item, dict):
                                continue
                            row = self._normalize_key_row(item)
                            if row.get("key"):
                                out.append(row)
                        self._keys = out
                    else:
                        self._keys = []
                else:
                    self._keys = []
                self._loaded = True
                logger.debug(f"[ApiKey] 加载了 {len(self._keys)} 个 API Key")
        except Exception as e:
            logger.error(f"[ApiKey] 加载失败: {e}")
            self._keys = []
            self._loaded = True # 即使加载失败也认为已尝试加载，防止后续保存清空数据（或者抛出异常）

    async def _save_data(self):
        """保存 API Keys"""
        if not self._loaded:
            logger.warning("[ApiKey] 尝试在数据未加载时保存，已取消操作以防覆盖数据")
            return
            
        try:
            # 确保目录存在
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            
            async with self._lock:
                content = orjson.dumps(self._keys, option=orjson.OPT_INDENT_2)
                await asyncio.to_thread(self.file_path.write_bytes, content)
        except Exception as e:
            logger.error(f"[ApiKey] 保存失败: {e}")

    def _normalize_limit(self, v: Any) -> int:
        """Normalize a daily limit value. -1 means unlimited."""
        if v is None or v == "":
            return -1
        try:
            n = int(v)
        except Exception:
            return -1
        return max(-1, n)

    def _normalize_key_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row or {})
        out["key"] = str(out.get("key") or "").strip()
        out["name"] = str(out.get("name") or "").strip()
        try:
            out["created_at"] = int(out.get("created_at") or int(time.time()))
        except Exception:
            out["created_at"] = int(time.time())
        out["is_active"] = bool(out.get("is_active", True))

        # Daily limits (-1 = unlimited)
        out["chat_limit"] = self._normalize_limit(out.get("chat_limit", -1))
        out["heavy_limit"] = self._normalize_limit(out.get("heavy_limit", -1))
        out["image_limit"] = self._normalize_limit(out.get("image_limit", -1))
        out["video_limit"] = self._normalize_limit(out.get("video_limit", -1))
        return out

    def _tz_offset_minutes(self) -> int:
        raw = (os.getenv("CACHE_RESET_TZ_OFFSET_MINUTES", "") or "").strip()
        try:
            n = int(raw)
        except Exception:
            n = 480
        return max(-720, min(840, n))

    def _day_str(self, at_ms: Optional[int] = None, tz_offset_minutes: Optional[int] = None) -> str:
        now_ms = int(at_ms if at_ms is not None else int(time.time() * 1000))
        offset = self._tz_offset_minutes() if tz_offset_minutes is None else int(tz_offset_minutes)
        dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc) + timedelta(minutes=offset)
        return dt.strftime("%Y-%m-%d")

    async def _load_usage_data(self):
        """Load per-day per-key usage counters."""
        if self._usage_loaded:
            return

        if not self.usage_path.exists():
            self._usage = {}
            self._usage_loaded = True
            return

        try:
            async with self._usage_lock:
                if self.usage_path.exists():
                    content = await asyncio.to_thread(self.usage_path.read_bytes)
                    if content:
                        data = orjson.loads(content)
                        if isinstance(data, dict):
                            # { day: { key: { chat_used, ... } } }
                            self._usage = data  # type: ignore[assignment]
                        else:
                            self._usage = {}
                    else:
                        self._usage = {}
                self._usage_loaded = True
        except Exception as e:
            logger.error(f"[ApiKey] Usage 加载失败: {e}")
            self._usage = {}
            self._usage_loaded = True

    async def _save_usage_data(self):
        if not self._usage_loaded:
            return
        try:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            async with self._usage_lock:
                content = orjson.dumps(self._usage, option=orjson.OPT_INDENT_2)
                await asyncio.to_thread(self.usage_path.write_bytes, content)
        except Exception as e:
            logger.error(f"[ApiKey] Usage 保存失败: {e}")

    def generate_key(self) -> str:
        """生成一个新的 sk- 开头的 key"""
        return f"sk-{secrets.token_urlsafe(24)}"

    def generate_name(self) -> str:
        """生成一个随机 key 名称"""
        return f"key-{secrets.token_urlsafe(6)}"

    async def add_key(
        self,
        name: str | None = None,
        key: str | None = None,
        limits: Optional[Dict[str, Any]] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """添加 API Key（支持自定义 key 与每日额度）"""
        await self.init()

        name_val = str(name or "").strip() or self.generate_name()
        key_val = str(key or "").strip() or self.generate_key()

        limits = limits or {}
        new_key: Dict[str, Any] = {
            "key": key_val,
            "name": name_val,
            "created_at": int(time.time()),
            "is_active": bool(is_active),
            "chat_limit": self._normalize_limit(limits.get("chat_limit", limits.get("chat_per_day", -1))),
            "heavy_limit": self._normalize_limit(limits.get("heavy_limit", limits.get("heavy_per_day", -1))),
            "image_limit": self._normalize_limit(limits.get("image_limit", limits.get("image_per_day", -1))),
            "video_limit": self._normalize_limit(limits.get("video_limit", limits.get("video_per_day", -1))),
        }

        # Ensure uniqueness
        if any(k.get("key") == key_val for k in self._keys):
            raise ValueError("Key already exists")

        self._keys.append(new_key)
        await self._save_data()
        logger.info(f"[ApiKey] 添加新Key: {name_val}")
        return new_key

    async def batch_add_keys(self, name_prefix: str, count: int) -> List[Dict]:
        """批量添加 API Key"""
        new_keys = []
        for i in range(1, count + 1):
            name = f"{name_prefix}-{i}" if count > 1 else name_prefix
            new_keys.append({
                "key": self.generate_key(),
                "name": name,
                "created_at": int(time.time()),
                "is_active": True,
                "chat_limit": -1,
                "heavy_limit": -1,
                "image_limit": -1,
                "video_limit": -1,
            })
        
        self._keys.extend(new_keys)
        await self._save_data()
        logger.info(f"[ApiKey] 批量添加 {count} 个 Key, 前缀: {name_prefix}")
        return new_keys

    async def delete_key(self, key: str) -> bool:
        """删除 API Key"""
        initial_len = len(self._keys)
        self._keys = [k for k in self._keys if k["key"] != key]
        
        if len(self._keys) != initial_len:
            await self._save_data()
            logger.info(f"[ApiKey] 删除Key: {key[:10]}...")
            return True
        return False

    async def batch_delete_keys(self, keys: List[str]) -> int:
        """批量删除 API Key"""
        initial_len = len(self._keys)
        self._keys = [k for k in self._keys if k["key"] not in keys]
        
        deleted_count = initial_len - len(self._keys)
        if deleted_count > 0:
            await self._save_data()
            logger.info(f"[ApiKey] 批量删除 {deleted_count} 个 Key")
        return deleted_count

    async def update_key_status(self, key: str, is_active: bool) -> bool:
        """更新 Key 状态"""
        for k in self._keys:
            if k["key"] == key:
                k["is_active"] = is_active
                await self._save_data()
                return True
        return False
        
    async def batch_update_keys_status(self, keys: List[str], is_active: bool) -> int:
        """批量更新 Key 状态"""
        updated_count = 0
        for k in self._keys:
            if k["key"] in keys:
                if k["is_active"] != is_active:
                    k["is_active"] = is_active
                    updated_count += 1
        
        if updated_count > 0:
            await self._save_data()
            logger.info(f"[ApiKey] 批量更新 {updated_count} 个 Key 状态为: {is_active}")
        return updated_count

    async def update_key_name(self, key: str, name: str) -> bool:
        """更新 Key 备注"""
        for k in self._keys:
            if k["key"] == key:
                k["name"] = name
                await self._save_data()
                return True
        return False

    async def update_key_limits(self, key: str, limits: Dict[str, Any]) -> bool:
        """更新 Key 每日额度（-1 表示不限）"""
        limits = limits or {}
        for k in self._keys:
            if k.get("key") != key:
                continue
            if "chat_limit" in limits or "chat_per_day" in limits:
                k["chat_limit"] = self._normalize_limit(limits.get("chat_limit", limits.get("chat_per_day")))
            if "heavy_limit" in limits or "heavy_per_day" in limits:
                k["heavy_limit"] = self._normalize_limit(limits.get("heavy_limit", limits.get("heavy_per_day")))
            if "image_limit" in limits or "image_per_day" in limits:
                k["image_limit"] = self._normalize_limit(limits.get("image_limit", limits.get("image_per_day")))
            if "video_limit" in limits or "video_per_day" in limits:
                k["video_limit"] = self._normalize_limit(limits.get("video_limit", limits.get("video_per_day")))
            await self._save_data()
            return True
        return False

    def get_key_row(self, key: str) -> Optional[Dict[str, Any]]:
        """获取 Key 原始记录（不要求 active）"""
        for k in self._keys:
            if k.get("key") == key:
                return self._normalize_key_row(k)
        return None

    async def usage_for_day(self, day: str) -> Dict[str, Dict[str, int]]:
        """返回指定 day 的 usage map: { key: {chat_used,...} }"""
        await self.init()
        if not self._usage_loaded:
            await self._load_usage_data()
        day_map = self._usage.get(day)
        return day_map if isinstance(day_map, dict) else {}

    async def usage_today(self) -> Tuple[str, Dict[str, Dict[str, int]]]:
        day = self._day_str()
        return day, await self.usage_for_day(day)

    async def consume_daily_usage(
        self,
        key: str,
        incs: Dict[str, int],
        tz_offset_minutes: Optional[int] = None,
    ) -> bool:
        """
        Consume per-day quota for the given API key.

        incs keys: chat_used/heavy_used/image_used/video_used
        """
        await self.init()
        row = self.get_key_row(key)
        if not row or not row.get("is_active"):
            # Unknown/disabled keys are already rejected by auth; keep best-effort safe here.
            return True

        if not self._usage_loaded:
            await self._load_usage_data()

        day = self._day_str(tz_offset_minutes=tz_offset_minutes)
        at_ms = int(time.time() * 1000)

        # Normalize incs
        normalized: Dict[str, int] = {}
        for k, v in (incs or {}).items():
            try:
                inc = int(v)
            except Exception:
                continue
            if inc <= 0:
                continue
            normalized[k] = inc
        if not normalized:
            return True

        limits = {
            "chat_used": int(row.get("chat_limit", -1)),
            "heavy_used": int(row.get("heavy_limit", -1)),
            "image_used": int(row.get("image_limit", -1)),
            "video_used": int(row.get("video_limit", -1)),
        }

        async with self._usage_lock:
            day_map = self._usage.get(day)
            if not isinstance(day_map, dict):
                day_map = {}
                self._usage[day] = day_map  # type: ignore[assignment]

            usage = day_map.get(key)
            if not isinstance(usage, dict):
                usage = {"chat_used": 0, "heavy_used": 0, "image_used": 0, "video_used": 0, "updated_at": at_ms}
                day_map[key] = usage  # type: ignore[assignment]

            # Check all limits first (atomic for multi-bucket)
            for bucket, inc in normalized.items():
                lim = int(limits.get(bucket, -1))
                used = int(usage.get(bucket, 0) or 0)
                if lim >= 0 and used + inc > lim:
                    return False

            # Apply
            for bucket, inc in normalized.items():
                usage[bucket] = int(usage.get(bucket, 0) or 0) + inc
            usage["updated_at"] = at_ms

        await self._save_usage_data()
        return True

    def validate_key(self, key: str) -> Optional[Dict]:
        """验证 Key，返回 Key 信息"""
        # 1. 检查全局配置的 Key (作为默认 admin key)
        global_key = str(get_config("app.api_key", "") or "").strip()
        if global_key and key == global_key:
            return {
                "key": global_key,
                "name": "默认管理员",
                "is_active": True,
                "is_admin": True
            }
            
        # 2. 检查多 Key 列表
        for k in self._keys:
            if k["key"] == key:
                if k["is_active"]:
                    return {**k, "is_admin": False} # 普通 Key 也可以视为非管理员? 暂不区分权限，只做身份识别
                return None
                
        return None

    def get_all_keys(self) -> List[Dict]:
        """获取所有 Keys"""
        return [self._normalize_key_row(k) for k in self._keys]


# 全局实例
api_key_manager = ApiKeyManager()

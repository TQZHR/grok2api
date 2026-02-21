"""请求日志审计 - 记录近期请求"""

import time
import asyncio
import orjson
from typing import List, Dict, Deque
from collections import deque
from dataclasses import dataclass, asdict
from pathlib import Path

from app.core.logger import logger

@dataclass
class RequestLog:
    id: str
    time: str
    timestamp: float
    ip: str
    model: str
    duration: float
    status: int
    key_name: str
    token_suffix: str
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    error: str = ""

class RequestLogger:
    """请求日志记录器"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, max_len: int = 1000):
        if hasattr(self, '_initialized'):
            return
            
        self.file_path = Path(__file__).parents[2] / "data" / "logs.json"
        self._logs: Deque[Dict] = deque(maxlen=max_len)
        self._lock = asyncio.Lock()
        self._loaded = False
        
        self._initialized = True

    async def init(self):
        """初始化加载数据"""
        if not self._loaded:
            await self._load_data()

    async def _load_data(self):
        """从磁盘加载日志数据"""
        if self._loaded:
            return

        if not self.file_path.exists():
            self._loaded = True
            return

        try:
            async with self._lock:
                content = await asyncio.to_thread(self.file_path.read_bytes)
                if content:
                    data = orjson.loads(content)
                    if isinstance(data, list):
                        self._logs.clear()
                        self._logs.extend(data)
                    self._loaded = True
                    logger.debug(f"[Logger] 加载日志成功: {len(self._logs)} 条")
        except Exception as e:
            logger.error(f"[Logger] 加载日志失败: {e}")
            self._loaded = True

    async def _save_data(self):
        """保存日志数据到磁盘"""
        if not self._loaded:
            return

        try:
            # 确保目录存在
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            
            async with self._lock:
                # 转换为列表保存
                content = orjson.dumps(list(self._logs))
                await asyncio.to_thread(self.file_path.write_bytes, content)
        except Exception as e:
            logger.error(f"[Logger] 保存日志失败: {e}")

    async def add_log(self, 
                     ip: str, 
                     model: str, 
                     duration: float, 
                     status: int, 
                     key_name: str, 
                     token_suffix: str = "",
                     total_tokens: int = 0,
                     input_tokens: int = 0,
                     output_tokens: int = 0,
                     reasoning_tokens: int = 0,
                     cached_tokens: int = 0,
                     error: str = ""):
        """添加日志"""
        if not self._loaded:
            await self.init()
            
        try:
            now = time.time()
            # 格式化时间
            time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            
            log = {
                "id": str(int(now * 1000)),
                "time": time_str,
                "timestamp": now,
                "ip": ip,
                "model": model,
                "duration": round(duration, 2),
                "status": status,
                "key_name": key_name,
                "token_suffix": token_suffix,
                "total_tokens": int(total_tokens or 0),
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "reasoning_tokens": int(reasoning_tokens or 0),
                "cached_tokens": int(cached_tokens or 0),
                "error": error
            }
            
            async with self._lock:
                self._logs.appendleft(log) # 最新的在前
                
            # 异步保存
            asyncio.create_task(self._save_data())
                
        except Exception as e:
            logger.error(f"[Logger] 记录日志失败: {e}")

    async def get_logs(self, limit: int = 1000) -> List[Dict]:
        """获取日志"""
        async with self._lock:
            return list(self._logs)[:limit]
    
    async def clear_logs(self):
        """清空日志"""
        async with self._lock:
            self._logs.clear()
        await self._save_data()


# 全局实例
request_logger = RequestLogger()

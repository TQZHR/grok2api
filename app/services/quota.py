"""
API Key daily quota enforcement (local/docker runtime)
"""

from __future__ import annotations

from typing import Optional, Dict

from app.core.config import get_config
from app.core.exceptions import AppException, ErrorType
from app.services.api_keys import api_key_manager
from app.services.grok.model import ModelService


async def enforce_daily_quota(
    api_key: Optional[str],
    model: str,
    *,
    image_count: Optional[int] = None,
) -> None:
    """
    Enforce per-day quotas for a non-admin API key.

    - chat/heavy/video: count by request (1)
    - image: count by generated images
      - chat endpoint + image model: charge 2 images per request
      - image endpoint: charge `image_count` (n)
    - heavy: consumes both heavy + chat buckets
    """

    token = str(api_key or "").strip()
    if not token:
        return

    global_key = str(get_config("app.api_key", "") or "").strip()
    if global_key and token == global_key:
        return

    model_info = ModelService.get(model)
    incs: Dict[str, int] = {}
    bucket_name = "chat"

    if model == "grok-4-heavy":
        incs = {"heavy_used": 1, "chat_used": 1}
        bucket_name = "heavy/chat"
    elif model_info and model_info.is_video:
        incs = {"video_used": 1}
        bucket_name = "video"
    elif model_info and model_info.is_image:
        # grok image model via chat endpoint: upstream usually returns up to 2 images
        incs = {"image_used": max(1, int(image_count or 2))}
        bucket_name = "image"
    else:
        incs = {"chat_used": 1}
        bucket_name = "chat"

    ok = await api_key_manager.consume_daily_usage(token, incs)
    if ok:
        return

    raise AppException(
        message=f"Daily quota exceeded: {bucket_name}",
        error_type=ErrorType.RATE_LIMIT.value,
        code="daily_quota_exceeded",
        status_code=429,
    )


__all__ = ["enforce_daily_quota"]


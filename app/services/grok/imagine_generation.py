"""
Shared helpers for experimental imagine generation flows.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, List, Optional

from app.core.exceptions import UpstreamException
from app.core.logger import logger
from app.services.grok.imagine_experimental import ImagineExperimentalService


def resolve_aspect_ratio(size: Optional[str]) -> str:
    value = str(size or "").strip().lower()
    if value in {"16:9", "9:16", "1:1", "2:3", "3:2"}:
        return value

    mapping = {
        "1024x1024": "1:1",
        "512x512": "1:1",
        "1024x576": "16:9",
        "1280x720": "16:9",
        "1536x864": "16:9",
        "576x1024": "9:16",
        "720x1280": "9:16",
        "864x1536": "9:16",
        "1024x1536": "2:3",
        "512x768": "2:3",
        "768x1024": "2:3",
        "1536x1024": "3:2",
        "768x512": "3:2",
        "1024x768": "3:2",
    }
    return mapping.get(value, "2:3")


def is_valid_image_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value != "error"


def dedupe_images(images: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for image in images:
        if not isinstance(image, str):
            continue
        if image in seen:
            continue
        seen.add(image)
        out.append(image)
    return out


async def gather_limited(
    task_factories: List[Callable[[], Awaitable[List[str]]]],
    max_concurrency: int,
) -> List[Any]:
    sem = asyncio.Semaphore(max(1, int(max_concurrency or 1)))

    async def _run(factory: Callable[[], Awaitable[List[str]]]) -> Any:
        async with sem:
            return await factory()

    return await asyncio.gather(*[_run(factory) for factory in task_factories], return_exceptions=True)


async def call_experimental_generation_once(
    token: str,
    prompt: str,
    response_format: str = "b64_json",
    n: int = 4,
    aspect_ratio: str = "2:3",
) -> List[str]:
    service = ImagineExperimentalService()
    raw_urls = await service.generate_ws(
        token=token,
        prompt=prompt,
        n=n,
        aspect_ratio=aspect_ratio,
    )
    return await service.convert_urls(token=token, urls=raw_urls, response_format=response_format)


async def collect_experimental_generation_images(
    token: str,
    prompt: str,
    n: int,
    response_format: str,
    aspect_ratio: str,
    concurrency: int,
) -> List[str]:
    calls_needed = max(1, (n + 3) // 4)
    task_factories: List[Callable[[], Awaitable[List[str]]]] = []
    remain = n
    for _ in range(calls_needed):
        target_n = max(1, min(4, remain))
        remain -= target_n
        task_factories.append(
            lambda target_n=target_n: call_experimental_generation_once(
                token,
                prompt,
                response_format=response_format,
                n=target_n,
                aspect_ratio=aspect_ratio,
            )
        )

    results = await gather_limited(
        task_factories,
        max_concurrency=min(calls_needed, max(1, int(concurrency or 1))),
    )
    all_images: List[str] = []
    for result in results:
        if isinstance(result, Exception):
            logger.warning(f"Experimental imagine websocket call failed: {result}")
            continue
        if isinstance(result, list):
            all_images.extend(result)

    all_images = dedupe_images(all_images)
    if not any(is_valid_image_value(item) for item in all_images):
        raise UpstreamException("Experimental imagine websocket returned no images")
    return all_images


__all__ = [
    "resolve_aspect_ratio",
    "is_valid_image_value",
    "dedupe_images",
    "gather_limited",
    "call_experimental_generation_once",
    "collect_experimental_generation_images",
]

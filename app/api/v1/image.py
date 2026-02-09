"""
Image Generation API 路由
"""

import asyncio
import base64
import random
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import orjson
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app.core.auth import verify_api_key
from app.core.config import get_config
from app.core.exceptions import AppException, ErrorType, UpstreamException, ValidationException
from app.core.logger import logger
from app.services.grok.assets import UploadService
from app.services.grok.chat import GrokChatService
from app.services.grok.imagine_experimental import (
    IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL,
    IMAGE_METHOD_LEGACY,
    ImagineExperimentalService,
    resolve_image_generation_method,
)
from app.services.grok.imagine_generation import (
    call_experimental_generation_once,
    collect_experimental_generation_images,
    dedupe_images as dedupe_imagine_images,
    is_valid_image_value as is_valid_imagine_image_value,
    resolve_aspect_ratio as resolve_imagine_aspect_ratio,
)
from app.services.grok.model import ModelService
from app.services.grok.processor import ImageCollectProcessor, ImageStreamProcessor
from app.services.quota import enforce_daily_quota
from app.services.request_stats import request_stats
from app.services.token import get_token_manager


router = APIRouter(tags=["Images"])
ALLOWED_RESPONSE_FORMATS = {"b64_json", "base64", "url"}


class ImageGenerationRequest(BaseModel):
    """Image generation request - OpenAI compatible."""

    prompt: str = Field(..., description="Image prompt")
    model: Optional[str] = Field("grok-imagine-1.0", description="Model name")
    n: Optional[int] = Field(1, ge=1, le=10, description="Image count (1-10)")
    size: Optional[str] = Field("1024x1024", description="Image size / ratio")
    quality: Optional[str] = Field("standard", description="Reserved")
    response_format: Optional[str] = Field(None, description="Response format")
    style: Optional[str] = Field(None, description="Reserved")
    stream: Optional[bool] = Field(False, description="Enable streaming")
    concurrency: Optional[int] = Field(1, ge=1, le=3, description="Experimental concurrency")


class ImageEditRequest(BaseModel):
    """Image edit request - OpenAI compatible."""

    prompt: str = Field(..., description="Edit prompt")
    model: Optional[str] = Field("grok-imagine-1.0-edit", description="Model name")
    image: Optional[Union[str, List[str]]] = Field(None, description="Input image(s)")
    n: Optional[int] = Field(1, ge=1, le=10, description="Image count (1-10)")
    size: Optional[str] = Field("1024x1024", description="Reserved")
    quality: Optional[str] = Field("standard", description="Reserved")
    response_format: Optional[str] = Field(None, description="Response format")
    style: Optional[str] = Field(None, description="Reserved")
    stream: Optional[bool] = Field(False, description="Enable streaming")


def validate_generation_request(request: ImageGenerationRequest):
    """Validate image generation request parameters."""
    model_id = request.model or "grok-imagine-1.0"
    if model_id != "grok-imagine-1.0":
        raise ValidationException(
            message="The model `grok-imagine-1.0` is required for image generation.",
            param="model",
            code="model_not_supported",
        )

    model_info = ModelService.get(model_id)
    if not model_info or not model_info.is_image:
        raise ValidationException(
            message=f"The model `{model_id}` is not supported for image generation.",
            param="model",
            code="model_not_supported",
        )

    if not request.prompt or not request.prompt.strip():
        raise ValidationException(
            message="Prompt cannot be empty",
            param="prompt",
            code="empty_prompt",
        )

    if request.n is None:
        request.n = 1
    if request.n < 1 or request.n > 10:
        raise ValidationException(
            message="n must be between 1 and 10",
            param="n",
            code="invalid_n",
        )

    if request.stream and request.n not in [1, 2]:
        raise ValidationException(
            message="Streaming is only supported when n=1 or n=2",
            param="stream",
            code="invalid_stream_n",
        )

    if request.concurrency is None:
        request.concurrency = 1
    if request.concurrency < 1 or request.concurrency > 3:
        raise ValidationException(
            message="concurrency must be between 1 and 3",
            param="concurrency",
            code="invalid_concurrency",
        )

    if request.response_format:
        candidate = request.response_format.lower()
        if candidate not in ALLOWED_RESPONSE_FORMATS:
            raise ValidationException(
                message=f"response_format must be one of {sorted(ALLOWED_RESPONSE_FORMATS)}",
                param="response_format",
                code="invalid_response_format",
            )


def validate_edit_request(request: ImageEditRequest, images: List[UploadFile]):
    """Validate image edit request parameters."""
    model_id = request.model or "grok-imagine-1.0-edit"
    if model_id != "grok-imagine-1.0-edit":
        raise ValidationException(
            message="The model `grok-imagine-1.0-edit` is required for image edits.",
            param="model",
            code="model_not_supported",
        )

    model_info = ModelService.get(model_id)
    if not model_info or not model_info.is_image:
        raise ValidationException(
            message=f"The model `{model_id}` is not supported for image edits.",
            param="model",
            code="model_not_supported",
        )

    if not request.prompt or not request.prompt.strip():
        raise ValidationException(
            message="Prompt cannot be empty",
            param="prompt",
            code="empty_prompt",
        )

    if request.n is None:
        request.n = 1
    if request.n < 1 or request.n > 10:
        raise ValidationException(
            message="n must be between 1 and 10",
            param="n",
            code="invalid_n",
        )

    if request.stream and request.n not in [1, 2]:
        raise ValidationException(
            message="Streaming is only supported when n=1 or n=2",
            param="stream",
            code="invalid_stream_n",
        )

    if request.response_format:
        candidate = request.response_format.lower()
        if candidate not in ALLOWED_RESPONSE_FORMATS:
            raise ValidationException(
                message=f"response_format must be one of {sorted(ALLOWED_RESPONSE_FORMATS)}",
                param="response_format",
                code="invalid_response_format",
            )

    if not images:
        raise ValidationException(
            message="Image is required",
            param="image",
            code="missing_image",
        )
    if len(images) > 16:
        raise ValidationException(
            message="Too many images. Maximum is 16.",
            param="image",
            code="invalid_image_count",
        )


def resolve_response_format(response_format: Optional[str]) -> str:
    candidate = response_format
    if not candidate:
        candidate = get_config("app.image_format", "url")
    if isinstance(candidate, str):
        candidate = candidate.lower()
    if candidate in ALLOWED_RESPONSE_FORMATS:
        return candidate
    raise ValidationException(
        message=f"response_format must be one of {sorted(ALLOWED_RESPONSE_FORMATS)}",
        param="response_format",
        code="invalid_response_format",
    )


def resolve_image_response_format(
    response_format: Optional[str],
    image_method: str,
) -> str:
    """
    Keep legacy behavior, but for experimental imagine path:
    if caller does not explicitly provide response_format and global default is `url`,
    prefer `b64_json` to avoid loopback URL rendering issues in local deployments.
    """
    raw = response_format if not isinstance(response_format, str) else response_format.strip()
    if not raw and image_method == IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL:
        default_format = str(get_config("app.image_format", "url") or "url").strip().lower()
        if default_format == "url":
            return "b64_json"
    return resolve_response_format(response_format)


def response_field_name(response_format: str) -> str:
    if response_format == "url":
        return "url"
    if response_format == "base64":
        return "base64"
    return "b64_json"


def _image_generation_method() -> str:
    return resolve_image_generation_method(
        get_config("grok.image_generation_method", IMAGE_METHOD_LEGACY)
    )


def resolve_aspect_ratio(size: Optional[str]) -> str:
    return resolve_imagine_aspect_ratio(size)


def _is_valid_image_value(value: Any) -> bool:
    return is_valid_imagine_image_value(value)


def _dedupe_images(images: List[str]) -> List[str]:
    return dedupe_imagine_images(images)


async def _gather_limited(
    task_factories: List[Callable[[], Awaitable[List[str]]]],
    max_concurrency: int,
) -> List[Any]:
    sem = asyncio.Semaphore(max(1, int(max_concurrency or 1)))

    async def _run(factory: Callable[[], Awaitable[List[str]]]) -> Any:
        async with sem:
            return await factory()

    return await asyncio.gather(*[_run(factory) for factory in task_factories], return_exceptions=True)


async def call_grok_legacy(
    token: str,
    prompt: str,
    model_info,
    file_attachments: Optional[List[str]] = None,
    response_format: str = "b64_json",
) -> List[str]:
    """
    调用 Grok 获取图片，返回图片列表
    """
    chat_service = GrokChatService()

    try:
        response = await chat_service.chat(
            token=token,
            message=prompt,
            model=model_info.grok_model,
            mode=model_info.model_mode,
            think=False,
            stream=True,
            file_attachments=file_attachments,
        )

        processor = ImageCollectProcessor(
            model_info.model_id,
            token,
            response_format=response_format,
        )
        return await processor.process(response)
    except Exception as e:
        logger.error(f"Grok image call failed: {e}")
        return []


async def call_grok_experimental_ws(
    token: str,
    prompt: str,
    response_format: str = "b64_json",
    n: int = 4,
    aspect_ratio: str = "2:3",
) -> List[str]:
    return await call_experimental_generation_once(
        token=token,
        prompt=prompt,
        response_format=response_format,
        n=n,
        aspect_ratio=aspect_ratio,
    )


async def call_grok_experimental_edit(
    token: str,
    prompt: str,
    model_id: str,
    file_uris: List[str],
    response_format: str = "b64_json",
) -> List[str]:
    service = ImagineExperimentalService()
    response = await service.chat_edit(token=token, prompt=prompt, file_uris=file_uris)
    processor = ImageCollectProcessor(
        model_id,
        token,
        response_format=response_format,
    )
    return await processor.process(response)


async def _collect_experimental_generation_images(
    token: str,
    prompt: str,
    n: int,
    response_format: str,
    aspect_ratio: str,
    concurrency: int,
) -> List[str]:
    return await collect_experimental_generation_images(
        token=token,
        prompt=prompt,
        n=n,
        response_format=response_format,
        aspect_ratio=aspect_ratio,
        concurrency=concurrency,
    )


async def _experimental_stream_generation(
    token: str,
    prompt: str,
    n: int,
    response_format: str,
    response_field: str,
    aspect_ratio: str,
    state: dict[str, Any],
):
    service = ImagineExperimentalService()
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    index_map: Dict[int, int] = {}
    map_lock = asyncio.Lock()
    next_output_index = 0

    async def _resolve_output_index(raw_index: int) -> int:
        nonlocal next_output_index
        async with map_lock:
            if raw_index not in index_map:
                index_map[raw_index] = min(next_output_index, max(0, n - 1))
                next_output_index += 1
            return index_map[raw_index]

    async def _progress_cb(raw_index: int, progress: float):
        idx = await _resolve_output_index(raw_index)
        await queue.put(
            _sse_event(
                "image_generation.partial_image",
                {
                    "type": "image_generation.partial_image",
                    response_field: "",
                    "index": idx,
                    "progress": max(0, min(100, int(progress))),
                },
            )
        )

    async def _completed_cb(raw_index: int, raw_url: str):
        idx = await _resolve_output_index(raw_index)
        converted = await service.convert_url(
            token=token,
            url=raw_url,
            response_format=response_format,
        )
        if not _is_valid_image_value(converted):
            return

        state["success"] = True
        await queue.put(
            _sse_event(
                "image_generation.completed",
                {
                    "type": "image_generation.completed",
                    response_field: converted,
                    "index": idx,
                    "usage": {
                        "total_tokens": 50,
                        "input_tokens": 25,
                        "output_tokens": 25,
                        "input_tokens_details": {"text_tokens": 5, "image_tokens": 20},
                    },
                },
            )
        )

    producer_error: Optional[Exception] = None

    async def _producer():
        nonlocal producer_error
        try:
            await service.generate_ws(
                token=token,
                prompt=prompt,
                n=n,
                aspect_ratio=aspect_ratio,
                progress_cb=_progress_cb,
                completed_cb=_completed_cb,
            )
        except Exception as exc:
            producer_error = exc
        finally:
            await queue.put(None)

    producer_task = asyncio.create_task(_producer())
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        await producer_task

    if not state.get("success", False):
        if isinstance(producer_error, Exception):
            raise producer_error
        raise UpstreamException("Experimental imagine websocket returned no images")


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {orjson.dumps(data).decode()}\n\n"


async def _synthetic_image_stream(
    selected_images: List[str],
    response_field: str,
):
    emitted = False
    for idx, image in enumerate(selected_images):
        if not isinstance(image, str) or not image or image == "error":
            continue
        emitted = True
        yield _sse_event(
            "image_generation.partial_image",
            {
                "type": "image_generation.partial_image",
                response_field: "",
                "index": idx,
                "progress": 100,
            },
        )
        yield _sse_event(
            "image_generation.completed",
            {
                "type": "image_generation.completed",
                response_field: image,
                "index": idx,
                "usage": {
                    "total_tokens": 50,
                    "input_tokens": 25,
                    "output_tokens": 25,
                    "input_tokens_details": {"text_tokens": 5, "image_tokens": 20},
                },
            },
        )
    if not emitted:
        yield _sse_event(
            "image_generation.completed",
            {
                "type": "image_generation.completed",
                response_field: "error",
                "index": 0,
                "usage": {
                    "total_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "input_tokens_details": {"text_tokens": 0, "image_tokens": 0},
                },
            },
        )


async def _record_request(model_id: str, success: bool):
    try:
        await request_stats.record_request(model_id, success=success)
    except Exception:
        pass


async def _get_token_for_model(model_id: str):
    """获取指定模型可用 token，失败时抛出统一异常"""
    try:
        token_mgr = await get_token_manager()
        await token_mgr.reload_if_stale()
        token = token_mgr.get_token_for_model(model_id)
    except Exception as e:
        logger.error(f"Failed to get token: {e}")
        await _record_request(model_id or "image", False)
        raise AppException(
            message="Internal service error obtaining token",
            error_type=ErrorType.SERVER.value,
            code="internal_error",
        )

    if not token:
        await _record_request(model_id or "image", False)
        raise AppException(
            message="No available tokens. Please try again later.",
            error_type=ErrorType.RATE_LIMIT.value,
            code="rate_limit_exceeded",
            status_code=429,
        )
    return token_mgr, token


def _pick_images(all_images: List[str], n: int) -> List[str]:
    if len(all_images) >= n:
        return random.sample(all_images, n)
    selected = all_images.copy()
    while len(selected) < n:
        selected.append("error")
    return selected


def _build_image_response(selected_images: List[str], response_field: str) -> JSONResponse:
    import time

    return JSONResponse(
        content={
            "created": int(time.time()),
            "data": [{response_field: img} for img in selected_images],
            "usage": {
                "total_tokens": 0 * len([img for img in selected_images if img != "error"]),
                "input_tokens": 0,
                "output_tokens": 0 * len([img for img in selected_images if img != "error"]),
                "input_tokens_details": {"text_tokens": 0, "image_tokens": 0},
            },
        }
    )


@router.get("/images/method")
async def get_image_generation_method():
    return {"image_generation_method": _image_generation_method()}


@router.post("/images/generations")
async def create_image(
    request: ImageGenerationRequest,
    api_key: Optional[str] = Depends(verify_api_key),
):
    """Image Generation API."""
    if request.stream is None:
        request.stream = False

    validate_generation_request(request)
    model_id = request.model or "grok-imagine-1.0"
    n = int(request.n or 1)
    concurrency = max(1, min(3, int(request.concurrency or 1)))
    image_method = _image_generation_method()
    response_format = resolve_image_response_format(request.response_format, image_method)
    request.response_format = response_format
    response_field = response_field_name(response_format)
    aspect_ratio = resolve_aspect_ratio(request.size)

    await enforce_daily_quota(api_key, model_id, image_count=n)
    token_mgr, token = await _get_token_for_model(model_id)
    model_info = ModelService.get(model_id)

    if request.stream:
        if image_method == IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL:
            stream_state: Dict[str, Any] = {"success": False}

            async def _wrapped_experimental_stream():
                try:
                    try:
                        async for chunk in _experimental_stream_generation(
                            token=token,
                            prompt=request.prompt,
                            n=n,
                            response_format=response_format,
                            response_field=response_field,
                            aspect_ratio=aspect_ratio,
                            state=stream_state,
                        ):
                            yield chunk
                    except Exception as stream_err:
                        logger.warning(
                            f"Experimental image generation realtime stream failed: {stream_err}. "
                            "Fallback to synthetic stream."
                        )
                        try:
                            all_images = await _collect_experimental_generation_images(
                                token=token,
                                prompt=request.prompt,
                                n=n,
                                response_format=response_format,
                                aspect_ratio=aspect_ratio,
                                concurrency=concurrency,
                            )
                            selected_images = _pick_images(_dedupe_images(all_images), n)
                            stream_state["success"] = any(
                                _is_valid_image_value(item) for item in selected_images
                            )
                            async for chunk in _synthetic_image_stream(selected_images, response_field):
                                yield chunk
                        except Exception as synthetic_err:
                            logger.warning(
                                f"Experimental synthetic stream failed: {synthetic_err}. "
                                "Fallback to legacy stream."
                            )
                            chat_service = GrokChatService()
                            response = await chat_service.chat(
                                token=token,
                                message=f"Image Generation: {request.prompt}",
                                model=model_info.grok_model,
                                mode=model_info.model_mode,
                                think=False,
                                stream=True,
                            )
                            processor = ImageStreamProcessor(
                                model_info.model_id,
                                token,
                                n=n,
                                response_format=response_format,
                            )
                            async for chunk in processor.process(response):
                                yield chunk
                            stream_state["success"] = True
                finally:
                    try:
                        if stream_state.get("success"):
                            await token_mgr.sync_usage(
                                token,
                                model_info.model_id,
                                consume_on_fail=True,
                                is_usage=True,
                            )
                            await _record_request(model_info.model_id, True)
                        else:
                            await _record_request(model_info.model_id, False)
                    except Exception:
                        pass

            return StreamingResponse(
                _wrapped_experimental_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        chat_service = GrokChatService()
        try:
            response = await chat_service.chat(
                token=token,
                message=f"Image Generation: {request.prompt}",
                model=model_info.grok_model,
                mode=model_info.model_mode,
                think=False,
                stream=True,
            )
        except Exception:
            await _record_request(model_info.model_id, False)
            raise

        processor = ImageStreamProcessor(
            model_info.model_id,
            token,
            n=n,
            response_format=response_format,
        )

        async def _wrapped_stream():
            completed = False
            try:
                async for chunk in processor.process(response):
                    yield chunk
                completed = True
            finally:
                try:
                    if completed:
                        await token_mgr.sync_usage(
                            token,
                            model_info.model_id,
                            consume_on_fail=True,
                            is_usage=True,
                        )
                        await _record_request(model_info.model_id, True)
                    else:
                        await _record_request(model_info.model_id, False)
                except Exception:
                    pass

        return StreamingResponse(
            _wrapped_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    all_images: List[str] = []
    if image_method == IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL:
        try:
            all_images = await _collect_experimental_generation_images(
                token=token,
                prompt=request.prompt,
                n=n,
                response_format=response_format,
                aspect_ratio=aspect_ratio,
                concurrency=concurrency,
            )
        except Exception as e:
            logger.warning(f"Experimental image generation failed, fallback to legacy: {e}")

    if not all_images:
        calls_needed = (n + 1) // 2
        task_factories: List[Callable[[], Awaitable[List[str]]]] = [
            lambda: call_grok_legacy(
                token,
                f"Image Generation: {request.prompt}",
                model_info,
                response_format=response_format,
            )
            for _ in range(calls_needed)
        ]
        results = await _gather_limited(
            task_factories,
            max_concurrency=min(calls_needed, concurrency),
        )

        all_images = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Concurrent call failed: {result}")
            elif isinstance(result, list):
                all_images.extend(result)

    selected_images = _pick_images(_dedupe_images(all_images), n)
    success = any(_is_valid_image_value(img) for img in selected_images)
    try:
        if success:
            await token_mgr.sync_usage(
                token,
                model_info.model_id,
                consume_on_fail=True,
                is_usage=True,
            )
        await _record_request(model_info.model_id, bool(success))
    except Exception:
        pass

    return _build_image_response(selected_images, response_field)


@router.post("/images/edits")
async def edit_image(
    prompt: str = Form(...),
    image: Optional[List[UploadFile]] = File(None),
    image_alias: Optional[List[UploadFile]] = File(None, alias="image[]"),
    model: Optional[str] = Form("grok-imagine-1.0-edit"),
    n: int = Form(1),
    size: str = Form("1024x1024"),
    quality: str = Form("standard"),
    response_format: Optional[str] = Form(None),
    style: Optional[str] = Form(None),
    stream: Optional[bool] = Form(False),
    api_key: Optional[str] = Depends(verify_api_key),
):
    """
    Image Edits API

    同官方 API 格式，仅支持 multipart/form-data 文件上传
    """
    try:
        edit_request = ImageEditRequest(
            prompt=prompt,
            model=model,
            n=n,
            size=size,
            quality=quality,
            response_format=response_format,
            style=style,
            stream=stream,
        )
    except ValidationError as exc:
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = first.get("loc", [])
            msg = first.get("msg", "Invalid request")
            code = first.get("type", "invalid_value")
            param_parts = [str(x) for x in loc if not (isinstance(x, int) or str(x).isdigit())]
            param = ".".join(param_parts) if param_parts else None
            raise ValidationException(message=msg, param=param, code=code)
        raise ValidationException(message="Invalid request", code="invalid_value")

    if edit_request.stream is None:
        edit_request.stream = False
    if edit_request.n is None:
        edit_request.n = 1

    image_method = _image_generation_method()
    response_format = resolve_image_response_format(edit_request.response_format, image_method)
    edit_request.response_format = response_format
    response_field = response_field_name(response_format)
    images = (image or []) + (image_alias or [])
    validate_edit_request(edit_request, images)

    model_id = edit_request.model or "grok-imagine-1.0-edit"
    n = int(edit_request.n or 1)

    await enforce_daily_quota(api_key, model_id, image_count=n)

    max_image_bytes = 50 * 1024 * 1024
    allowed_types = {"image/png", "image/jpeg", "image/webp", "image/jpg"}
    image_payloads: List[str] = []

    for item in images:
        content = await item.read()
        await item.close()
        if not content:
            raise ValidationException(
                message="File content is empty",
                param="image",
                code="empty_file",
            )
        if len(content) > max_image_bytes:
            raise ValidationException(
                message="Image file too large. Maximum is 50MB.",
                param="image",
                code="file_too_large",
            )

        mime = (item.content_type or "").lower()
        if mime == "image/jpg":
            mime = "image/jpeg"
        ext = Path(item.filename or "").suffix.lower()
        if mime not in allowed_types:
            if ext in (".jpg", ".jpeg"):
                mime = "image/jpeg"
            elif ext == ".png":
                mime = "image/png"
            elif ext == ".webp":
                mime = "image/webp"
            else:
                raise ValidationException(
                    message="Unsupported image type. Supported: png, jpg, webp.",
                    param="image",
                    code="invalid_image_type",
                )

        image_payloads.append(f"data:{mime};base64,{base64.b64encode(content).decode()}")

    token_mgr, token = await _get_token_for_model(model_id)
    model_info = ModelService.get(model_id)

    file_ids: List[str] = []
    file_uris: List[str] = []
    upload_service = UploadService()
    try:
        for payload in image_payloads:
            file_id, file_uri = await upload_service.upload(payload, token)
            if file_id:
                file_ids.append(file_id)
            if file_uri:
                file_uris.append(file_uri)
    finally:
        await upload_service.close()

    if edit_request.stream:
        if image_method == IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL:
            try:
                service = ImagineExperimentalService()
                response = await service.chat_edit(
                    token=token,
                    prompt=edit_request.prompt,
                    file_uris=file_uris,
                )
                processor = ImageStreamProcessor(
                    model_info.model_id,
                    token,
                    n=n,
                    response_format=response_format,
                )

                async def _wrapped_experimental_stream():
                    completed = False
                    try:
                        async for chunk in processor.process(response):
                            yield chunk
                        completed = True
                    finally:
                        try:
                            if completed:
                                await token_mgr.sync_usage(
                                    token,
                                    model_info.model_id,
                                    consume_on_fail=True,
                                    is_usage=True,
                                )
                                await _record_request(model_info.model_id, True)
                            else:
                                await _record_request(model_info.model_id, False)
                        except Exception:
                            pass

                return StreamingResponse(
                    _wrapped_experimental_stream(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
                )
            except Exception as e:
                logger.warning(f"Experimental image edit stream failed, fallback to legacy: {e}")

        chat_service = GrokChatService()
        try:
            response = await chat_service.chat(
                token=token,
                message=f"Image Edit: {edit_request.prompt}",
                model=model_info.grok_model,
                mode=model_info.model_mode,
                think=False,
                stream=True,
                file_attachments=file_ids,
            )
        except Exception:
            await _record_request(model_info.model_id, False)
            raise

        processor = ImageStreamProcessor(
            model_info.model_id,
            token,
            n=n,
            response_format=response_format,
        )

        async def _wrapped_stream():
            completed = False
            try:
                async for chunk in processor.process(response):
                    yield chunk
                completed = True
            finally:
                try:
                    if completed:
                        await token_mgr.sync_usage(
                            token,
                            model_info.model_id,
                            consume_on_fail=True,
                            is_usage=True,
                        )
                        await _record_request(model_info.model_id, True)
                    else:
                        await _record_request(model_info.model_id, False)
                except Exception:
                    pass

        return StreamingResponse(
            _wrapped_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    all_images: List[str] = []
    if image_method == IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL:
        try:
            calls_needed = (n + 1) // 2
            if calls_needed == 1:
                all_images = await call_grok_experimental_edit(
                    token=token,
                    prompt=edit_request.prompt,
                    model_id=model_info.model_id,
                    file_uris=file_uris,
                    response_format=response_format,
                )
            else:
                tasks = [
                    call_grok_experimental_edit(
                        token=token,
                        prompt=edit_request.prompt,
                        model_id=model_info.model_id,
                        file_uris=file_uris,
                        response_format=response_format,
                    )
                    for _ in range(calls_needed)
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.warning(f"Experimental image edit call failed: {result}")
                    elif isinstance(result, list):
                        all_images.extend(result)
            if not all_images:
                raise UpstreamException("Experimental image edit returned no images")
        except Exception as e:
            logger.warning(f"Experimental image edit failed, fallback to legacy: {e}")

    if not all_images:
        calls_needed = (n + 1) // 2
        if calls_needed == 1:
            all_images = await call_grok_legacy(
                token,
                f"Image Edit: {edit_request.prompt}",
                model_info,
                file_attachments=file_ids,
                response_format=response_format,
            )
        else:
            tasks = [
                call_grok_legacy(
                    token,
                    f"Image Edit: {edit_request.prompt}",
                    model_info,
                    file_attachments=file_ids,
                    response_format=response_format,
                )
                for _ in range(calls_needed)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_images = []
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Concurrent call failed: {result}")
                elif isinstance(result, list):
                    all_images.extend(result)

    selected_images = _pick_images(all_images, n)
    success = any(isinstance(img, str) and img and img != "error" for img in selected_images)
    try:
        if success:
            await token_mgr.sync_usage(
                token,
                model_info.model_id,
                consume_on_fail=True,
                is_usage=True,
            )
        await _record_request(model_info.model_id, bool(success))
    except Exception:
        pass

    return _build_image_response(selected_images, response_field)


__all__ = ["router"]

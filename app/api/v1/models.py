"""
Models API 路由
"""

import time

from fastapi import APIRouter, HTTPException

from app.services.grok.model import ModelService


router = APIRouter(tags=["Models"])


@router.get("/models")
async def list_models():
    """OpenAI 兼容 models 列表接口"""
    ts = int(time.time())
    data = [
        {
            "id": m.model_id,
            "object": "model",
            "created": ts,
            "owned_by": "grok2api",
            "display_name": m.display_name,
            "description": m.description,
        }
        for m in ModelService.list()
    ]
    return {"object": "list", "data": data}


@router.get("/models/{model_id}")
async def get_model(model_id: str):
    """OpenAI compatible: single model detail."""
    m = ModelService.get(model_id)
    if not m:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    ts = int(time.time())
    return {
        "id": m.model_id,
        "object": "model",
        "created": ts,
        "owned_by": "grok2api",
        "display_name": m.display_name,
        "description": m.description,
    }


__all__ = ["router"]

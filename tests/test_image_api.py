from io import BytesIO

import pytest
from fastapi import UploadFile
from pydantic import ValidationError

from app.api.v1.image import (
    ImageEditRequest,
    ImageGenerationRequest,
    resolve_aspect_ratio,
    validate_edit_request,
)
from app.core.exceptions import ValidationException
from app.services.grok.model import ModelService


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ("1024x1024", "1:1"),
        ("1024x576", "16:9"),
        ("576x1024", "9:16"),
        ("1024x1536", "2:3"),
        ("1536x1024", "3:2"),
        ("3:2", "3:2"),
        ("unknown-size", "2:3"),
    ],
)
def test_resolve_aspect_ratio(size: str, expected: str):
    assert resolve_aspect_ratio(size) == expected


def test_image_generation_request_concurrency_bounds():
    req = ImageGenerationRequest(prompt="demo", concurrency=1)
    assert req.concurrency == 1

    with pytest.raises(ValidationError):
        ImageGenerationRequest(prompt="demo", concurrency=0)

    with pytest.raises(ValidationError):
        ImageGenerationRequest(prompt="demo", concurrency=4)


def test_edit_model_mapping_exists():
    model = ModelService.get("grok-imagine-1.0-edit")
    assert model is not None
    assert model.grok_model == "imagine-image-edit"
    assert model.is_image is True


def test_validate_edit_request_rejects_legacy_model():
    req = ImageEditRequest(prompt="edit", model="grok-imagine-1.0", n=1, stream=False)
    file = UploadFile(filename="a.png", file=BytesIO(b"x"))

    with pytest.raises(ValidationException) as exc:
        validate_edit_request(req, [file])

    assert exc.value.code == "model_not_supported"


def test_validate_edit_request_accepts_new_model():
    req = ImageEditRequest(prompt="edit", model="grok-imagine-1.0-edit", n=1, stream=False)
    file = UploadFile(filename="a.png", file=BytesIO(b"x"))
    validate_edit_request(req, [file])

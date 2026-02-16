import pytest

from app.services.grok.imagine_experimental import (
    IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL,
    IMAGE_METHOD_LEGACY,
    resolve_image_generation_method,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("legacy", IMAGE_METHOD_LEGACY),
        ("imagine_ws_experimental", IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL),
        ("imagine_ws", IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL),
        ("experimental", IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL),
        ("new", IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL),
        ("new_method", IMAGE_METHOD_IMAGINE_WS_EXPERIMENTAL),
        ("unknown", IMAGE_METHOD_LEGACY),
        ("", IMAGE_METHOD_LEGACY),
        (None, IMAGE_METHOD_LEGACY),
    ],
)
def test_resolve_image_generation_method(raw, expected):
    assert resolve_image_generation_method(raw) == expected

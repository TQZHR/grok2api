from app.services.grok.model import ModelService


def test_model_catalog_contains_latest_models():
    model_ids = {m.model_id for m in ModelService.list()}
    expected = {
        "grok-3",
        "grok-3-mini",
        "grok-3-thinking",
        "grok-4",
        "grok-4-mini",
        "grok-4-thinking",
        "grok-4-heavy",
        "grok-4.1-mini",
        "grok-4.1-fast",
        "grok-4.1-expert",
        "grok-4.1-thinking",
        "grok-4.20-beta",
        "grok-imagine-1.0",
        "grok-imagine-1.0-edit",
        "grok-imagine-1.0-video",
    }
    assert expected.issubset(model_ids)


def test_removed_models_are_not_exposed():
    model_ids = {m.model_id for m in ModelService.list()}
    removed = {"grok-3-fast", "grok-4-fast", "grok-4.1"}
    assert model_ids.isdisjoint(removed)


def test_grok_420_mapping():
    model = ModelService.get("grok-4.20-beta")
    assert model is not None
    assert model.grok_model == "grok-420"
    assert model.model_mode == "MODEL_MODE_GROK_420"

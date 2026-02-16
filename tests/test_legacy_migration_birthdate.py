import asyncio

from app.core import legacy_migration
import app.core.config as config_module
import app.core.storage as storage_module
import app.services.register.services as services_module


class _DummyStorage:
    def __init__(self, token_data):
        self._token_data = token_data

    async def load_tokens(self):
        return self._token_data


def test_migration_v2_runs_tos_birth_nsfw_in_order(monkeypatch, tmp_path):
    call_order = []

    class _UserAgreementService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def accept_tos_version(self, sso, sso_rw, impersonate):
            call_order.append("tos")
            return {"ok": True}

    class _BirthDateService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def set_birth_date(self, sso, sso_rw, impersonate):
            call_order.append("birth")
            return {"ok": True}

    class _NsfwSettingsService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def enable_nsfw(self, sso, sso_rw, impersonate):
            call_order.append("nsfw")
            return {"ok": True}

    monkeypatch.setattr(storage_module, "get_storage", lambda: _DummyStorage({"ssoBasic": ["token-a"]}))
    monkeypatch.setattr(config_module, "get_config", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(services_module, "UserAgreementService", _UserAgreementService)
    monkeypatch.setattr(services_module, "BirthDateService", _BirthDateService)
    monkeypatch.setattr(services_module, "NsfwSettingsService", _NsfwSettingsService)

    result = asyncio.run(legacy_migration.migrate_legacy_account_settings(concurrency=1, data_dir=tmp_path))

    assert result["migrated"] is True
    assert result["total"] == 1
    assert result["ok"] == 1
    assert result["failed"] == 0
    assert call_order == ["tos", "birth", "nsfw"]
    assert (tmp_path / ".locks" / "legacy_accounts_tos_birth_nsfw_v2.done").exists()


def test_migration_v2_skips_when_done_marker_exists(tmp_path):
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "legacy_accounts_tos_birth_nsfw_v2.done").write_text("done", encoding="utf-8")

    result = asyncio.run(legacy_migration.migrate_legacy_account_settings(concurrency=1, data_dir=tmp_path))

    assert result == {"migrated": False, "reason": "already_done"}


def test_migration_v2_counts_fail_when_birth_step_fails(monkeypatch, tmp_path):
    call_order = []

    class _UserAgreementService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def accept_tos_version(self, sso, sso_rw, impersonate):
            call_order.append(f"tos:{sso}")
            return {"ok": True}

    class _BirthDateService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def set_birth_date(self, sso, sso_rw, impersonate):
            call_order.append(f"birth:{sso}")
            return {"ok": sso != "token-fail"}

    class _NsfwSettingsService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def enable_nsfw(self, sso, sso_rw, impersonate):
            call_order.append(f"nsfw:{sso}")
            return {"ok": True}

    monkeypatch.setattr(
        storage_module,
        "get_storage",
        lambda: _DummyStorage({"ssoBasic": ["token-fail", "token-ok"]}),
    )
    monkeypatch.setattr(config_module, "get_config", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(services_module, "UserAgreementService", _UserAgreementService)
    monkeypatch.setattr(services_module, "BirthDateService", _BirthDateService)
    monkeypatch.setattr(services_module, "NsfwSettingsService", _NsfwSettingsService)

    result = asyncio.run(legacy_migration.migrate_legacy_account_settings(concurrency=1, data_dir=tmp_path))

    assert result["migrated"] is True
    assert result["total"] == 2
    assert result["ok"] == 1
    assert result["failed"] == 1
    assert "nsfw:token-fail" not in call_order
    assert "nsfw:token-ok" in call_order

import asyncio

from app.services.register import account_settings_refresh as refresh_module


class _DummyTokenManager:
    def __init__(self) -> None:
        self.success_calls = []
        self.invalid_calls = []
        self.commit_calls = 0

    async def mark_token_account_settings_success(self, token: str, save: bool = True) -> bool:
        self.success_calls.append((token, save))
        return True

    async def set_token_invalid(self, token: str, reason: str = "", save: bool = True) -> bool:
        self.invalid_calls.append((token, reason, save))
        return True

    async def commit(self):
        self.commit_calls += 1


def test_parse_sso_pair_variants():
    assert refresh_module.parse_sso_pair("token-a") == ("token-a", "token-a")
    assert refresh_module.parse_sso_pair("sso=token-a") == ("token-a", "token-a")
    assert refresh_module.parse_sso_pair("sso=token-a;sso-rw=token-b") == ("token-a", "token-b")
    assert refresh_module.parse_sso_pair("sso-rw=token-b; sso=token-a") == ("token-a", "token-b")
    assert refresh_module.parse_sso_pair("foo=bar") == ("foo=bar", "foo=bar")


def test_refresh_tokens_runs_tos_birth_nsfw_in_order(monkeypatch):
    calls = []

    class _UserAgreementService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def accept_tos_version(self, sso, sso_rw, impersonate):
            calls.append(f"tos:{sso}:{sso_rw}:{impersonate}")
            return {"ok": True}

    class _BirthDateService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def set_birth_date(self, sso, sso_rw, impersonate):
            calls.append(f"birth:{sso}:{sso_rw}:{impersonate}")
            return {"ok": True}

    class _NsfwSettingsService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def enable_nsfw(self, sso, sso_rw, impersonate):
            calls.append(f"nsfw:{sso}:{sso_rw}:{impersonate}")
            return {"ok": True}

    monkeypatch.setattr(refresh_module, "UserAgreementService", _UserAgreementService)
    monkeypatch.setattr(refresh_module, "BirthDateService", _BirthDateService)
    monkeypatch.setattr(refresh_module, "NsfwSettingsService", _NsfwSettingsService)

    mgr = _DummyTokenManager()
    service = refresh_module.AccountSettingsRefreshService(mgr, cf_clearance="")
    result = asyncio.run(service.refresh_tokens(tokens=["sso=token-a"], concurrency=1, retries=3))

    assert result["summary"] == {"total": 1, "success": 1, "failed": 0, "invalidated": 0}
    assert result["failed"] == []
    assert mgr.success_calls == [("token-a", False)]
    assert mgr.invalid_calls == []
    assert mgr.commit_calls == 1
    assert calls == [
        "tos:token-a:token-a:chrome120",
        "birth:token-a:token-a:chrome120",
        "nsfw:token-a:token-a:chrome120",
    ]


def test_refresh_tokens_retries_then_invalidates(monkeypatch):
    attempt = {"count": 0}

    class _UserAgreementService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def accept_tos_version(self, sso, sso_rw, impersonate):
            attempt["count"] += 1
            return {"ok": False, "error": "forbidden"}

    class _BirthDateService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def set_birth_date(self, sso, sso_rw, impersonate):
            raise AssertionError("birth step should not run when TOS fails")

    class _NsfwSettingsService:
        def __init__(self, cf_clearance=""):
            self.cf_clearance = cf_clearance

        def enable_nsfw(self, sso, sso_rw, impersonate):
            raise AssertionError("nsfw step should not run when TOS fails")

    monkeypatch.setattr(refresh_module, "UserAgreementService", _UserAgreementService)
    monkeypatch.setattr(refresh_module, "BirthDateService", _BirthDateService)
    monkeypatch.setattr(refresh_module, "NsfwSettingsService", _NsfwSettingsService)

    mgr = _DummyTokenManager()
    service = refresh_module.AccountSettingsRefreshService(mgr, cf_clearance="")
    result = asyncio.run(service.refresh_tokens(tokens=["token-a"], concurrency=1, retries=3))

    assert result["summary"] == {"total": 1, "success": 0, "failed": 1, "invalidated": 1}
    assert len(result["failed"]) == 1
    assert result["failed"][0]["token"] == "token-a"
    assert result["failed"][0]["step"] == "tos"
    assert result["failed"][0]["attempts"] == 4
    assert attempt["count"] == 4
    assert mgr.success_calls == []
    assert len(mgr.invalid_calls) == 1
    assert mgr.invalid_calls[0][0] == "token-a"
    assert mgr.commit_calls == 1

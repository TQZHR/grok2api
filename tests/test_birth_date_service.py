import datetime
import re

from app.services.register.services import birth_date_service as birth_service_module


class _DummyResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_generate_birth_date_format_and_age_range():
    pattern = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T16:00:00\.000Z$")
    today = datetime.date.today()

    for _ in range(200):
        value = birth_service_module.generate_random_birthdate()
        match = pattern.match(value)
        assert match is not None

        year, month, day = map(int, match.groups())
        assert 1 <= month <= 12
        assert 1 <= day <= 28

        # Service uses year-only offset; keep assertion aligned with implementation.
        year_age = today.year - year
        assert 20 <= year_age <= 40


def test_set_birth_date_missing_sso_or_sso_rw():
    service = birth_service_module.BirthDateService()

    missing_sso = service.set_birth_date(sso="", sso_rw="x", impersonate="chrome120")
    assert missing_sso == {
        "ok": False,
        "status_code": None,
        "response_text": "",
        "error": "missing sso",
    }

    missing_sso_rw = service.set_birth_date(sso="x", sso_rw="", impersonate="chrome120")
    assert missing_sso_rw == {
        "ok": False,
        "status_code": None,
        "response_text": "",
        "error": "missing sso-rw",
    }


def test_set_birth_date_success_http_200(monkeypatch):
    captured = {}

    def _fake_post(url, headers, cookies, json, impersonate, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["cookies"] = cookies
        captured["json"] = json
        captured["impersonate"] = impersonate
        captured["timeout"] = timeout
        return _DummyResponse(200, "ok")

    monkeypatch.setattr(
        birth_service_module,
        "generate_random_birthdate",
        lambda: "1998-01-15T16:00:00.000Z",
    )
    monkeypatch.setattr(birth_service_module.requests, "post", _fake_post)

    service = birth_service_module.BirthDateService()
    result = service.set_birth_date(
        sso="sso-token",
        sso_rw="sso-rw-token",
        impersonate="chrome120",
        user_agent="UnitTest-UA",
        timeout=9,
    )

    assert result == {
        "ok": True,
        "status_code": 200,
        "response_text": "ok",
        "error": None,
    }
    assert captured["url"] == "https://grok.com/rest/auth/set-birth-date"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["headers"]["origin"] == "https://grok.com"
    assert captured["headers"]["referer"] == "https://grok.com/"
    assert captured["headers"]["user-agent"] == "UnitTest-UA"
    assert captured["cookies"]["sso"] == "sso-token"
    assert captured["cookies"]["sso-rw"] == "sso-rw-token"
    assert captured["json"] == {"birthDate": "1998-01-15T16:00:00.000Z"}
    assert captured["impersonate"] == "chrome120"
    assert captured["timeout"] == 9


def test_set_birth_date_http_error_non_200(monkeypatch):
    monkeypatch.setattr(
        birth_service_module.requests,
        "post",
        lambda *args, **kwargs: _DummyResponse(403, "forbidden"),
    )

    service = birth_service_module.BirthDateService()
    result = service.set_birth_date(sso="s", sso_rw="rw", impersonate="chrome120")

    assert result["ok"] is False
    assert result["status_code"] == 403
    assert result["response_text"] == "forbidden"
    assert result["error"] == "HTTP 403"


def test_set_birth_date_request_exception(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(birth_service_module.requests, "post", _raise)

    service = birth_service_module.BirthDateService()
    result = service.set_birth_date(sso="s", sso_rw="rw", impersonate="chrome120")

    assert result["ok"] is False
    assert result["status_code"] is None
    assert result["response_text"] == ""
    assert "boom" in (result["error"] or "")

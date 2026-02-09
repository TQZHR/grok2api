from __future__ import annotations

import datetime
import random
from typing import Any, Dict, Optional

from curl_cffi import requests

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def generate_random_birthdate() -> str:
    """Generate a random birth date between 20 and 40 years old."""
    today = datetime.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


class BirthDateService:
    """Set account birth date via Grok REST API."""

    def __init__(self, cf_clearance: str = ""):
        self.cf_clearance = (cf_clearance or "").strip()

    def set_birth_date(
        self,
        sso: str,
        sso_rw: str,
        impersonate: str,
        user_agent: Optional[str] = None,
        cf_clearance: Optional[str] = None,
        timeout: int = 15,
    ) -> Dict[str, Any]:
        if not sso:
            return {
                "ok": False,
                "status_code": None,
                "response_text": "",
                "error": "missing sso",
            }
        if not sso_rw:
            return {
                "ok": False,
                "status_code": None,
                "response_text": "",
                "error": "missing sso-rw",
            }

        url = "https://grok.com/rest/auth/set-birth-date"
        cookies = {
            "sso": sso,
            "sso-rw": sso_rw,
        }
        clearance = (cf_clearance if cf_clearance is not None else self.cf_clearance).strip()
        if clearance:
            cookies["cf_clearance"] = clearance

        headers = {
            "content-type": "application/json",
            "origin": "https://grok.com",
            "referer": "https://grok.com/",
            "user-agent": user_agent or DEFAULT_USER_AGENT,
        }
        payload = {"birthDate": generate_random_birthdate()}

        try:
            response = requests.post(
                url,
                headers=headers,
                cookies=cookies,
                json=payload,
                impersonate=impersonate or "chrome120",
                timeout=timeout,
            )
            status_code = response.status_code
            response_text = response.text or ""
            ok = status_code == 200
            return {
                "ok": ok,
                "status_code": status_code,
                "response_text": response_text,
                "error": None if ok else f"HTTP {status_code}",
            }
        except Exception as e:
            return {
                "ok": False,
                "status_code": None,
                "response_text": "",
                "error": str(e),
            }

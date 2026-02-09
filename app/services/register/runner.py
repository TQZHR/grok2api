"""Grok account registration runner."""
from __future__ import annotations

import concurrent.futures
import random
import re
import string
import struct
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from app.core.logger import logger
from app.services.register.services import (
    EmailService,
    TurnstileService,
    UserAgreementService,
    BirthDateService,
    NsfwSettingsService,
)


SITE_URL = "https://accounts.x.ai"
DEFAULT_IMPERSONATE = "chrome120"

CHROME_PROFILES = [
    {"impersonate": "chrome110", "version": "110.0.0.0", "brand": "chrome"},
    {"impersonate": "chrome119", "version": "119.0.0.0", "brand": "chrome"},
    {"impersonate": "chrome120", "version": "120.0.0.0", "brand": "chrome"},
    {"impersonate": "edge99", "version": "99.0.1150.36", "brand": "edge"},
    {"impersonate": "edge101", "version": "101.0.1210.47", "brand": "edge"},
]


def _random_chrome_profile() -> Tuple[str, str]:
    profile = random.choice(CHROME_PROFILES)
    if profile.get("brand") == "edge":
        chrome_major = profile["version"].split(".")[0]
        chrome_version = f"{chrome_major}.0.0.0"
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{chrome_version} Safari/537.36 Edg/{profile['version']}"
        )
    else:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{profile['version']} Safari/537.36"
        )
    return profile["impersonate"], ua


def _generate_random_name() -> str:
    length = random.randint(4, 6)
    return random.choice(string.ascii_uppercase) + "".join(
        random.choice(string.ascii_lowercase) for _ in range(length - 1)
    )


def _generate_random_string(length: int = 15) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def _encode_grpc_message(field_id: int, string_value: str) -> bytes:
    key = (field_id << 3) | 2
    value_bytes = string_value.encode("utf-8")
    payload = struct.pack("B", key) + struct.pack("B", len(value_bytes)) + value_bytes
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def _encode_grpc_message_verify(email: str, code: str) -> bytes:
    p1 = struct.pack("B", (1 << 3) | 2) + struct.pack("B", len(email)) + email.encode("utf-8")
    p2 = struct.pack("B", (2 << 3) | 2) + struct.pack("B", len(code)) + code.encode("utf-8")
    payload = p1 + p2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


class RegisterRunner:
    """Threaded registration runner."""

    def __init__(
        self,
        target_count: int = 100,
        thread_count: int = 8,
        on_success: Optional[Callable[[str, str, str, int, int], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self.target_count = max(1, int(target_count))
        self.thread_count = max(1, int(thread_count))
        self.on_success = on_success
        self.on_error = on_error
        self.stop_event = stop_event or threading.Event()

        self._post_lock = threading.Lock()
        self._result_lock = threading.Lock()

        self._success_count = 0
        self._start_time = 0.0
        self._tokens: List[str] = []
        self._accounts: List[Dict[str, str]] = []

        self._config: Dict[str, Optional[str]] = {
            "site_key": "0x4AAAAAAAhr9JGVDZbrZOo0",
            "action_id": None,
            "state_tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22(app)%22%2C%7B%22children%22%3A%5B%22(auth)%22%2C%7B%22children%22%3A%5B%22sign-up%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2C%22%2Fsign-up%22%2C%22refresh%22%5D%7D%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D",
        }

    @property
    def success_count(self) -> int:
        return self._success_count

    @property
    def tokens(self) -> List[str]:
        return list(self._tokens)

    @property
    def accounts(self) -> List[Dict[str, str]]:
        return list(self._accounts)

    def _record_success(self, email: str, password: str, token: str) -> None:
        with self._result_lock:
            if self._success_count >= self.target_count:
                if not self.stop_event.is_set():
                    self.stop_event.set()
                return

            self._success_count += 1
            self._tokens.append(token)
            self._accounts.append({"email": email, "password": password, "token": token})

            avg = (time.time() - self._start_time) / max(1, self._success_count)
            logger.info(
                "Register success: {} | sso={}... | avg={:.1f}s ({}/{})",
                email,
                token[:12],
                avg,
                self._success_count,
                self.target_count,
            )

            if self.on_success:
                try:
                    self.on_success(email, password, token, self._success_count, self.target_count)
                except Exception:
                    pass

            if self._success_count >= self.target_count and not self.stop_event.is_set():
                self.stop_event.set()

    def _record_error(self, message: str) -> None:
        if self.on_error:
            try:
                self.on_error(message)
            except Exception:
                pass

    def _init_config(self) -> None:
        logger.info("Register: initializing action config...")
        start_url = f"{SITE_URL}/sign-up"

        with curl_requests.Session(impersonate=DEFAULT_IMPERSONATE) as session:
            html = session.get(start_url, timeout=15).text

            key_match = re.search(r'sitekey":"(0x4[a-zA-Z0-9_-]+)"', html)
            if key_match:
                self._config["site_key"] = key_match.group(1)

            tree_match = re.search(r'next-router-state-tree":"([^"]+)"', html)
            if tree_match:
                self._config["state_tree"] = tree_match.group(1)

            soup = BeautifulSoup(html, "html.parser")
            js_urls = [
                urljoin(start_url, script["src"])
                for script in soup.find_all("script", src=True)
                if "_next/static" in script["src"]
            ]
            for js_url in js_urls:
                js_content = session.get(js_url, timeout=15).text
                match = re.search(r"7f[a-fA-F0-9]{40}", js_content)
                if match:
                    self._config["action_id"] = match.group(0)
                    logger.info("Register: Action ID found: {}", self._config["action_id"])
                    break

        if not self._config.get("action_id"):
            raise RuntimeError("Register init failed: missing action_id")

    def _send_email_code(self, session: curl_requests.Session, email: str) -> bool:
        url = f"{SITE_URL}/auth_mgmt.AuthManagement/CreateEmailValidationCode"
        data = _encode_grpc_message(1, email)
        headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "origin": SITE_URL,
            "referer": f"{SITE_URL}/sign-up?redirect=grok-com",
        }
        try:
            res = session.post(url, data=data, headers=headers, timeout=15)
            return res.status_code == 200
        except Exception as exc:
            self._record_error(f"send code error: {email} - {exc}")
            return False

    def _verify_email_code(self, session: curl_requests.Session, email: str, code: str) -> bool:
        url = f"{SITE_URL}/auth_mgmt.AuthManagement/VerifyEmailValidationCode"
        data = _encode_grpc_message_verify(email, code)
        headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "origin": SITE_URL,
            "referer": f"{SITE_URL}/sign-up?redirect=grok-com",
        }
        try:
            res = session.post(url, data=data, headers=headers, timeout=15)
            return res.status_code == 200
        except Exception as exc:
            self._record_error(f"verify code error: {email} - {exc}")
            return False

    def _register_single_thread(self) -> None:
        time.sleep(random.uniform(0, 5))

        try:
            email_service = EmailService()
            turnstile_service = TurnstileService()
            user_agreement_service = UserAgreementService()
            birth_date_service = BirthDateService()
            nsfw_service = NsfwSettingsService()
        except Exception as exc:
            self._record_error(f"service init failed: {exc}")
            return

        final_action_id = self._config.get("action_id")
        if not final_action_id:
            self._record_error("missing action id")
            return

        while not self.stop_event.is_set():
            try:
                impersonate_fingerprint, account_user_agent = _random_chrome_profile()

                with curl_requests.Session(impersonate=impersonate_fingerprint) as session:
                    try:
                        session.get(SITE_URL, timeout=10)
                    except Exception:
                        pass

                    password = _generate_random_string()

                    jwt, email = email_service.create_email()
                    if not email:
                        self._record_error("create_email failed")
                        time.sleep(5)
                        continue

                    if self.stop_event.is_set():
                        return

                    if not self._send_email_code(session, email):
                        self._record_error(f"send_email_code failed: {email}")
                        time.sleep(5)
                        continue

                    verify_code = None
                    for _ in range(30):
                        time.sleep(1)
                        if self.stop_event.is_set():
                            return
                        content = email_service.fetch_first_email(jwt)
                        if content:
                            match = re.search(r">([A-Z0-9]{3}-[A-Z0-9]{3})<", content)
                            if match:
                                verify_code = match.group(1).replace("-", "")
                                break

                    if not verify_code:
                        self._record_error(f"verify_code not received: {email}")
                        time.sleep(3)
                        continue

                    if not self._verify_email_code(session, email, verify_code):
                        self._record_error(f"verify_email_code failed: {email}")
                        time.sleep(3)
                        continue

                    for _ in range(3):
                        if self.stop_event.is_set():
                            return

                        try:
                            task_id = turnstile_service.create_task(f"{SITE_URL}/sign-up", self._config["site_key"] or "")
                        except Exception as exc:
                            self._record_error(f"turnstile create_task failed: {exc}")
                            time.sleep(2)
                            continue

                        token = turnstile_service.get_response(task_id, stop_event=self.stop_event)

                        if not token:
                            self._record_error(f"turnstile failed: {turnstile_service.last_error or 'no token'}")
                            time.sleep(2)
                            continue

                        headers = {
                            "user-agent": account_user_agent,
                            "accept": "text/x-component",
                            "content-type": "text/plain;charset=UTF-8",
                            "origin": SITE_URL,
                            "referer": f"{SITE_URL}/sign-up",
                            "cookie": f"__cf_bm={session.cookies.get('__cf_bm','')}",
                            "next-router-state-tree": self._config["state_tree"] or "",
                            "next-action": final_action_id,
                        }
                        payload = [
                            {
                                "emailValidationCode": verify_code,
                                "createUserAndSessionRequest": {
                                    "email": email,
                                    "givenName": _generate_random_name(),
                                    "familyName": _generate_random_name(),
                                    "clearTextPassword": password,
                                    "tosAcceptedVersion": "$undefined",
                                },
                                "turnstileToken": token,
                                "promptOnDuplicateEmail": True,
                            }
                        ]

                        with self._post_lock:
                            res = session.post(
                                f"{SITE_URL}/sign-up",
                                json=payload,
                                headers=headers,
                                timeout=20,
                            )

                        if res.status_code != 200:
                            self._record_error(f"sign_up http {res.status_code}")
                            time.sleep(3)
                            continue

                        match = re.search(r'(https://[^" \s]+set-cookie\?q=[^:" \s]+)1:', res.text)
                        if not match:
                            self._record_error("sign_up missing set-cookie redirect")
                            break

                        verify_url = match.group(1)
                        session.get(verify_url, allow_redirects=True, timeout=15)

                        sso = session.cookies.get("sso")
                        sso_rw = session.cookies.get("sso-rw")
                        if not sso:
                            self._record_error("sign_up missing sso cookie")
                            break

                        tos_result = user_agreement_service.accept_tos_version(
                            sso=sso,
                            sso_rw=sso_rw or "",
                            impersonate=impersonate_fingerprint,
                            user_agent=account_user_agent,
                        )
                        if not tos_result.get("ok") or not tos_result.get("hex_reply"):
                            self._record_error(f"accept_tos failed: {tos_result.get('error') or 'unknown'}")
                            break

                        birth_result = birth_date_service.set_birth_date(
                            sso=sso,
                            sso_rw=sso_rw or "",
                            impersonate=impersonate_fingerprint,
                            user_agent=account_user_agent,
                        )
                        if not birth_result.get("ok"):
                            self._record_error(
                                f"set_birth_date failed: {birth_result.get('error') or 'unknown'}"
                            )
                            break

                        nsfw_result = nsfw_service.enable_nsfw(
                            sso=sso,
                            sso_rw=sso_rw or "",
                            impersonate=impersonate_fingerprint,
                            user_agent=account_user_agent,
                        )
                        if not nsfw_result.get("ok") or not nsfw_result.get("hex_reply"):
                            self._record_error(f"enable_nsfw failed: {nsfw_result.get('error') or 'unknown'}")
                            break

                        self._record_success(email, password, sso)
                        break

            except Exception as exc:
                self._record_error(f"thread error: {str(exc)[:80]}")
                time.sleep(3)

    def run(self) -> List[str]:
        """Run the registration process and return collected tokens."""
        self._init_config()
        self._start_time = time.time()

        logger.info("Register: starting {} threads, target {}", self.thread_count, self.target_count)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.thread_count) as executor:
            futures = [executor.submit(self._register_single_thread) for _ in range(self.thread_count)]
            concurrent.futures.wait(futures)

        return list(self._tokens)

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from streamlit_cookies_manager import EncryptedCookieManager

from .config import settings


COOKIE_NAME = "crm_intermares_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7


def _sign(username: str, expires_at: int) -> str:
    payload = f"{username}:{expires_at}".encode("utf-8")
    return hmac.new(settings.app_secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _make_token(username: str) -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    sig = _sign(username, expires_at)
    return f"{username}:{expires_at}:{sig}"


def _parse_token(token: str) -> Optional[str]:
    try:
        username, expires_str, sig = token.split(":", 2)
        expires_at = int(expires_str)
    except Exception:
        return None

    if expires_at < int(time.time()):
        return None

    expected = _sign(username, expires_at)
    if not hmac.compare_digest(sig, expected):
        return None

    return username


def get_cookie_manager() -> EncryptedCookieManager:
    return EncryptedCookieManager(
        prefix="crm_intermares/",
        password=settings.app_secret_key,
    )


def authenticate(username: str, password: str) -> bool:
    return hmac.compare_digest(username, settings.app_username) and hmac.compare_digest(
        password, settings.app_password
    )


def persist_login(cookies: EncryptedCookieManager, username: str) -> None:
    cookies[COOKIE_NAME] = _make_token(username)
    cookies.save()


def restore_login(cookies: EncryptedCookieManager) -> Optional[str]:
    token = cookies.get(COOKIE_NAME)
    if not token:
        return None
    return _parse_token(token)


def logout(cookies: EncryptedCookieManager) -> None:
    if COOKIE_NAME in cookies:
        del cookies[COOKIE_NAME]
        cookies.save()

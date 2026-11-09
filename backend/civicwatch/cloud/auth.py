"""Authentication for the cloud deployment.

Two independent credentials exist, and neither is ever stored in plaintext:

``CIVICWATCH_ADMIN_TOKEN``
    A long random secret set as an environment variable on Vercel. Humans
    exchange it for a signed, HttpOnly session cookie at ``/api/auth/session``.
    The cookie lets the browser load evidence images and export downloads,
    which cannot send an ``Authorization`` header.

``connector token``
    Generated per connector, shown once when the connector is created, and
    stored only as a SHA-256 hash. The local connector sends it as a Bearer
    token on every HTTPS request.

There are no user accounts: this is a single-operator municipal deployment,
documented as such. Rotate either token by changing the environment variable
(admin) or recreating the connector row.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Any

SESSION_COOKIE = "cw_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


class AuthError(RuntimeError):
    """Raised when a credential is missing or invalid."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def admin_token() -> str:
    return (os.environ.get("CIVICWATCH_ADMIN_TOKEN") or "").strip()


def auth_configured() -> bool:
    return bool(admin_token())


def cloud_mode() -> bool:
    """True when the process is serving the hosted dashboard."""
    explicit = (os.environ.get("CIVICWATCH_MODE") or "").strip().lower()
    if explicit in {"cloud", "local"}:
        return explicit == "cloud"
    return bool(os.environ.get("VERCEL")) or bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_connector_token() -> str:
    return "cwconn_" + secrets.token_urlsafe(32)


def token_digest(raw: str | None) -> str | None:
    if not raw:
        return None
    return hash_token(raw)


def compare(raw: str | None, expected: str | None) -> bool:
    if not raw or not expected:
        return False
    return hmac.compare_digest(raw, expected)


def bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


# ---------------------------------------------------------------------------
# Dashboard session cookie (signed with a key derived from the admin token)
# ---------------------------------------------------------------------------

def _cookie_key() -> bytes:
    token = admin_token()
    if not token:
        raise AuthError("CIVICWATCH_ADMIN_TOKEN is not configured")
    return hashlib.sha256(("civicwatch-session:" + token).encode("utf-8")).digest()


def issue_session(now: int | None = None) -> tuple[str, int]:
    """Return ``(cookie_value, expires_at_epoch)``."""
    expires = int(now if now is not None else time.time()) + SESSION_TTL_SECONDS
    payload = str(expires)
    signature = hmac.new(_cookie_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}", expires


def verify_session(value: str | None, now: int | None = None) -> bool:
    if not value or "." not in value:
        return False
    payload, _, signature = value.partition(".")
    try:
        expires = int(payload)
    except ValueError:
        return False
    if expires < int(now if now is not None else time.time()):
        return False
    try:
        expected = hmac.new(_cookie_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    except AuthError:
        return False
    return hmac.compare_digest(signature, expected)


def request_is_dashboard_authenticated(request: Any) -> bool:
    """Bearer admin token or a valid session cookie."""
    token = bearer_token(request.headers.get("authorization"))
    if token and compare(token, admin_token()):
        return True
    return verify_session(request.cookies.get(SESSION_COOKIE))


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------

_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@")


def redact_url_credentials(value: str) -> str:
    """``rtsp://user:pass@host/...`` → ``rtsp://***@host/...``."""
    if not value:
        return value
    return _URL_CREDENTIALS.sub(lambda m: f"{m.group('scheme')}***@", value)


def sanitize_label(value: str | None) -> str | None:
    """Last line of defence before a camera label leaves the local machine."""
    if not value:
        return value
    return redact_url_credentials(str(value))


FORBIDDEN_PAYLOAD_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "rtsp_url",
    "rtsp",
    "stream_url",
    "credentials",
    "username",
    "user",
    "camera_url",
    "source_value",
}


def contains_credentials(fragment: str | None) -> bool:
    """Detect ``scheme://user:pass@`` inside arbitrary text."""
    if not fragment:
        return False
    return bool(_URL_CREDENTIALS.search(str(fragment)))

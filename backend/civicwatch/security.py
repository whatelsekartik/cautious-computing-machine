"""Credential encryption, log redaction and localhost-only API enforcement."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import allow_remote, get_paths

log = logging.getLogger("civicwatch.security")

_KEY_FILENAME = "credentials.key"
_cached_fernet: Fernet | None = None


def _key_path() -> Path:
    return get_paths().secrets / _KEY_FILENAME


def _load_key() -> bytes:
    path = _key_path()
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    os.chmod(path, 0o600)
    return key


def _fernet() -> Fernet:
    global _cached_fernet
    if _cached_fernet is None:
        _cached_fernet = Fernet(_load_key())
    return _cached_fernet


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:  # pragma: no cover - corrupted key
        raise ValueError("Stored credential could not be decrypted") from exc


# --- redaction ------------------------------------------------------------
# Credentials must never reach logs, API responses or error reports.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]*://)(?P<user>[^/@\s:]+)(?::(?P<pw>[^/@\s]*))?@")
_QUERY_SECRETS = re.compile(r"(?i)(password|passwd|pwd|token|secret|key)=([^&\s]+)")


def redact(value: str | None) -> str:
    """Remove userinfo and secret query parameters from any URL-ish string."""
    if not value:
        return ""
    text = _URL_CREDENTIALS.sub(lambda m: f"{m.group('scheme')}***:***@", value)
    return _QUERY_SECRETS.sub(lambda m: f"{m.group(1)}=***", text)


def redact_url_for_display(value: str | None) -> str:
    """Show only scheme://host:port/path — never the credentials."""
    if not value:
        return ""
    return redact(value)


def mask_rtsp(value: str) -> str:
    text = redact(value)
    # Also hide paths of IP cameras (some embed tokens in the path).
    return re.sub(r"(/[^?\s]*?)(\?|$)", lambda m: ("/***" + (m.group(2) or "")), text, count=1)


class LocalhostOnlyMiddleware(BaseHTTPMiddleware):
    """Reject API traffic that does not originate from this machine.

    CivicWatch handles CCTV footage and camera credentials, so the HTTP API is
    localhost-only by default. ``CIVICWATCH_ALLOW_REMOTE=1`` exists solely for
    container previews used during development and is never set by the macOS
    launchers.
    """

    async def dispatch(self, request: Request, call_next):
        if allow_remote() or not request.url.path.startswith("/api"):
            return await call_next(request)
        host = request.client.host if request.client else ""
        if not _is_local(host):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "CivicWatch only accepts API requests from this machine "
                        f"(received from {host}). Run it locally on 127.0.0.1."
                    )
                },
            )
        return await call_next(request)


def _is_local(host: str) -> bool:
    if host in {"localhost", "testclient", ""}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback

from __future__ import annotations

from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
import binascii
import hashlib
import hmac
import secrets
import time

_SCRYPT_MAX_N = 2**20  # 1M; legitimate value is 16384 (2**14)
_SCRYPT_MAX_R = 64
_SCRYPT_MAX_P = 64

from fastapi import Request
from starlette.responses import Response

from alpaca_bot.config import Settings


_SESSION_COOKIE_NAME = "alpaca_bot_operator_session"
_SESSION_TTL_SECONDS = 12 * 60 * 60


def auth_enabled(settings: Settings) -> bool:
    return settings.dashboard_auth_enabled


def current_operator(request: Request, *, settings: Settings) -> str | None:
    operator = _parse_session_operator(request, settings=settings)
    if operator is not None:
        return operator
    credentials = _parse_basic_credentials(request)
    if credentials is None:
        return None
    username, password = credentials
    if authenticate_operator(settings=settings, username=username, password=password):
        return settings.dashboard_auth_username or username
    return None


def build_operator_session_token(
    *,
    settings: Settings,
    username: str,
    now: int | None = None,
) -> str:
    configured_username = settings.dashboard_auth_username
    configured_hash = settings.dashboard_auth_password_hash
    if configured_username is None or configured_hash is None:
        return ""
    normalized_username = _normalize_username(username)
    canonical_username = _normalize_username(configured_username)
    if not hmac.compare_digest(normalized_username, canonical_username):
        return ""
    expires_at = int(now if now is not None else time.time()) + _SESSION_TTL_SECONDS
    payload = f"{canonical_username}\n{expires_at}"
    signature = hmac.HMAC(
        configured_hash.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    token = urlsafe_b64encode(f"{payload}\n{signature}".encode("utf-8"))
    return token.decode("ascii")


def set_operator_session(
    response: Response,
    *,
    settings: Settings,
    username: str,
) -> None:
    token = build_operator_session_token(settings=settings, username=username)
    if not token:
        return
    response.set_cookie(
        key=_SESSION_COOKIE_NAME,
        value=token,
        max_age=_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=True,
    )


def clear_operator_session(response: Response) -> None:
    response.delete_cookie(
        _SESSION_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=True,
    )


def authenticate_operator(
    *,
    settings: Settings,
    username: str,
    password: str,
) -> bool:
    configured_username = settings.dashboard_auth_username
    configured_hash = settings.dashboard_auth_password_hash
    if configured_username is None or configured_hash is None:
        return False
    if not hmac.compare_digest(username.strip().lower(), configured_username.strip().lower()):
        return False
    return verify_password(password=password, encoded_hash=configured_hash)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    resolved_salt = salt or secrets.token_bytes(16)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=resolved_salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt$16384$8$1${resolved_salt.hex()}${derived_key.hex()}"


def verify_password(*, password: str, encoded_hash: str) -> bool:
    try:
        scheme, n_value, r_value, p_value, salt_hex, key_hex = encoded_hash.split("$", 5)
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected_key = bytes.fromhex(key_hex)
        n, r, p = int(n_value), int(r_value), int(p_value)
    except ValueError:
        return False
    if not (2 <= n <= _SCRYPT_MAX_N and (n & (n - 1)) == 0):
        return False
    if not (1 <= r <= _SCRYPT_MAX_R and 1 <= p <= _SCRYPT_MAX_P):
        return False
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected_key),
    )
    return hmac.compare_digest(derived_key, expected_key)


def _parse_basic_credentials(request: Request) -> tuple[str, str] | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Basic "):
        return None
    encoded = authorization[6:].strip()
    try:
        decoded = b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


def _parse_session_operator(request: Request, *, settings: Settings) -> str | None:
    cookies = getattr(request, "cookies", {}) or {}
    token = cookies.get(_SESSION_COOKIE_NAME, "")
    if not token:
        return None
    configured_username = settings.dashboard_auth_username
    configured_hash = settings.dashboard_auth_password_hash
    if configured_username is None or configured_hash is None:
        return None
    try:
        decoded = urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    try:
        username, expires_at_raw, signature = decoded.split("\n", 2)
        expires_at = int(expires_at_raw)
    except ValueError:
        return None
    if expires_at < int(time.time()):
        return None
    canonical_username = _normalize_username(configured_username)
    if not hmac.compare_digest(_normalize_username(username), canonical_username):
        return None
    payload = f"{canonical_username}\n{expires_at}"
    expected_signature = hmac.HMAC(
        configured_hash.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    return configured_username


def csrf_token_for_session(request: Request, *, settings: Settings, action: str = "form") -> str:
    cookies = getattr(request, "cookies", {}) or {}
    session_cookie = cookies.get(_SESSION_COOKIE_NAME, "")
    configured_hash = settings.dashboard_auth_password_hash or ""
    key = configured_hash.encode("utf-8") or b"no-auth"
    message = f"{session_cookie}\n{action}".encode("utf-8")
    return hmac.HMAC(key, message, hashlib.sha256).hexdigest()


def validate_csrf_token(
    request: Request,
    token: str,
    *,
    settings: Settings,
    action: str = "form",
) -> bool:
    expected = csrf_token_for_session(request, settings=settings, action=action)
    return hmac.compare_digest(token, expected)


def _normalize_username(value: str) -> str:
    return value.strip().lower()

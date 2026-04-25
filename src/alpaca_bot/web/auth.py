from __future__ import annotations

from base64 import b64decode
import binascii
import hashlib
import hmac
import secrets

_SCRYPT_MAX_N = 2**20  # 1M; legitimate value is 16384 (2**14)
_SCRYPT_MAX_R = 64
_SCRYPT_MAX_P = 64

from fastapi import Request

from alpaca_bot.config import Settings


def auth_enabled(settings: Settings) -> bool:
    return settings.dashboard_auth_enabled


def current_operator(request: Request, *, settings: Settings) -> str | None:
    credentials = _parse_basic_credentials(request)
    if credentials is None:
        return None
    username, password = credentials
    if authenticate_operator(settings=settings, username=username, password=password):
        return username
    return None


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

from __future__ import annotations

from base64 import b64encode
from types import SimpleNamespace

from alpaca_bot.config import Settings
from alpaca_bot.web.auth import (
    _SESSION_COOKIE_NAME,
    _parse_basic_credentials,
    authenticate_operator,
    build_operator_session_token,
    current_operator,
    hash_password,
    verify_password,
)


def make_settings(**overrides: str) -> Settings:
    values = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://example",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "DAILY_SMA_PERIOD": "20",
        "BREAKOUT_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
        "RELATIVE_VOLUME_THRESHOLD": "1.5",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "RISK_PER_TRADE_PCT": "0.0025",
        "MAX_POSITION_PCT": "0.05",
        "MAX_OPEN_POSITIONS": "3",
        "DAILY_LOSS_LIMIT_PCT": "0.01",
        "STOP_LIMIT_BUFFER_PCT": "0.001",
        "BREAKOUT_STOP_BUFFER_PCT": "0.001",
        "ENTRY_STOP_PRICE_BUFFER": "0.01",
        "ENTRY_WINDOW_START": "10:00",
        "ENTRY_WINDOW_END": "15:30",
        "FLATTEN_TIME": "15:45",
    }
    values.update(overrides)
    return Settings.from_env(values)


def make_request(
    authorization: str = "",
    *,
    cookies: dict[str, str] | None = None,
) -> SimpleNamespace:
    class Headers:
        def __init__(self, value: str) -> None:
            self._value = value

        def get(self, key: str, default: str = "") -> str:
            return self._value if key == "authorization" else default

    return SimpleNamespace(headers=Headers(authorization), cookies=cookies or {})


def _basic_header(username: str, password: str) -> str:
    return "Basic " + b64encode(f"{username}:{password}".encode()).decode()


# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------


def test_hash_password_round_trips_with_verify() -> None:
    encoded = hash_password("secret-password", salt=bytes.fromhex("00" * 16))

    assert encoded.startswith("scrypt$16384$8$1$")
    assert verify_password(password="secret-password", encoded_hash=encoded) is True
    assert verify_password(password="wrong-password", encoded_hash=encoded) is False


def test_hash_password_uses_provided_salt() -> None:
    salt = bytes.fromhex("deadbeef" * 4)
    encoded = hash_password("pw", salt=salt)

    assert "deadbeefdeadbeefdeadbeefdeadbeef" in encoded


def test_hash_password_generates_unique_salt_each_call() -> None:
    h1 = hash_password("pw")
    h2 = hash_password("pw")

    assert h1 != h2


def test_hash_password_output_format() -> None:
    encoded = hash_password("pw", salt=bytes.fromhex("00" * 16))
    parts = encoded.split("$")

    assert len(parts) == 6
    scheme, n, r, p, _salt, _key = parts
    assert scheme == "scrypt"
    assert int(n) == 16384
    assert int(r) == 8
    assert int(p) == 1


def test_verify_password_returns_false_on_malformed_hash() -> None:
    assert verify_password(password="pw", encoded_hash="not-a-valid-hash") is False
    assert verify_password(password="pw", encoded_hash="only$three$parts") is False


def test_verify_password_returns_false_on_wrong_scheme() -> None:
    encoded = hash_password("pw", salt=bytes.fromhex("00" * 16))
    bad_scheme = "argon2" + encoded[6:]

    assert verify_password(password="pw", encoded_hash=bad_scheme) is False


def test_verify_password_returns_false_on_invalid_hex() -> None:
    assert (
        verify_password(password="pw", encoded_hash="scrypt$16384$8$1$ZZZZ$key") is False
    )


def test_verify_password_returns_false_on_non_integer_params() -> None:
    # int("abc") raises ValueError — must be caught and return False, not propagate
    assert verify_password(password="pw", encoded_hash="scrypt$abc$8$1$aabb$ccdd") is False
    assert verify_password(password="pw", encoded_hash="scrypt$16384$8$1e5$aabb$ccdd") is False


def test_verify_password_returns_false_on_out_of_bounds_scrypt_params() -> None:
    salt_hex = "00" * 16
    key_hex = "ff" * 32
    # n too large (> 2**20) — prevents DoS via extreme memory allocation
    assert verify_password(password="pw", encoded_hash=f"scrypt$2097152$8$1${salt_hex}${key_hex}") is False
    # n not a power of two — invalid scrypt parameter
    assert verify_password(password="pw", encoded_hash=f"scrypt$16383$8$1${salt_hex}${key_hex}") is False
    # r too large
    assert verify_password(password="pw", encoded_hash=f"scrypt$16384$128$1${salt_hex}${key_hex}") is False
    # p too large
    assert verify_password(password="pw", encoded_hash=f"scrypt$16384$8$128${salt_hex}${key_hex}") is False


# ---------------------------------------------------------------------------
# _parse_basic_credentials
# ---------------------------------------------------------------------------


def test_parse_basic_credentials_returns_none_when_no_header() -> None:
    assert _parse_basic_credentials(make_request("")) is None


def test_parse_basic_credentials_returns_none_without_basic_prefix() -> None:
    assert _parse_basic_credentials(make_request("Bearer token")) is None


def test_parse_basic_credentials_returns_none_on_invalid_base64() -> None:
    assert _parse_basic_credentials(make_request("Basic !!!not-base64!!!")) is None


def test_parse_basic_credentials_returns_none_without_colon_separator() -> None:
    no_colon = b64encode(b"usernameonly").decode()
    assert _parse_basic_credentials(make_request(f"Basic {no_colon}")) is None


def test_parse_basic_credentials_splits_on_first_colon_only() -> None:
    encoded = b64encode(b"user:pass:with:colons").decode()
    result = _parse_basic_credentials(make_request(f"Basic {encoded}"))

    assert result == ("user", "pass:with:colons")


def test_parse_basic_credentials_returns_username_and_password() -> None:
    result = _parse_basic_credentials(make_request(_basic_header("alice", "secret")))

    assert result == ("alice", "secret")


# ---------------------------------------------------------------------------
# authenticate_operator
# ---------------------------------------------------------------------------


def test_authenticate_operator_returns_false_when_username_not_configured() -> None:
    settings = make_settings()  # dashboard_auth_username=None

    assert (
        authenticate_operator(settings=settings, username="any", password="any") is False
    )


def test_authenticate_operator_returns_false_when_hash_not_configured() -> None:
    # Can't set auth_enabled=True without both fields, so test via SimpleNamespace
    fake_settings = SimpleNamespace(
        dashboard_auth_username="user@example.com",
        dashboard_auth_password_hash=None,
    )

    assert (
        authenticate_operator(settings=fake_settings, username="user@example.com", password="pw")
        is False
    )


def test_authenticate_operator_username_match_is_case_insensitive() -> None:
    pw = "test-password"
    encoded = hash_password(pw, salt=bytes.fromhex("00" * 16))
    fake_settings = SimpleNamespace(
        dashboard_auth_username="User@Example.COM",
        dashboard_auth_password_hash=encoded,
    )

    assert authenticate_operator(settings=fake_settings, username="user@example.com", password=pw)


def test_authenticate_operator_returns_false_on_wrong_password() -> None:
    encoded = hash_password("correct", salt=bytes.fromhex("00" * 16))
    fake_settings = SimpleNamespace(
        dashboard_auth_username="u@example.com",
        dashboard_auth_password_hash=encoded,
    )

    assert (
        authenticate_operator(settings=fake_settings, username="u@example.com", password="wrong")
        is False
    )


def test_current_operator_returns_username_from_valid_session_cookie() -> None:
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="operator@example.com",
        DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
            "secret-password",
            salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        ),
    )
    token = build_operator_session_token(settings=settings, username="operator@example.com")

    result = current_operator(
        make_request(cookies={_SESSION_COOKIE_NAME: token}),
        settings=settings,
    )

    assert result == "operator@example.com"


def test_current_operator_returns_none_for_expired_session_cookie(monkeypatch) -> None:
    settings = make_settings(
        DASHBOARD_AUTH_ENABLED="true",
        DASHBOARD_AUTH_USERNAME="operator@example.com",
        DASHBOARD_AUTH_PASSWORD_HASH=hash_password(
            "secret-password",
            salt=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        ),
    )
    token = build_operator_session_token(
        settings=settings,
        username="operator@example.com",
        now=1_700_000_000,
    )

    monkeypatch.setattr("alpaca_bot.web.auth.time.time", lambda: 1_700_000_000 + 86_400)

    result = current_operator(
        make_request(cookies={_SESSION_COOKIE_NAME: token}),
        settings=settings,
    )

    assert result is None

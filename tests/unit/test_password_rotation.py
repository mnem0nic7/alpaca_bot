from pathlib import Path

from alpaca_bot.web import password_rotate_cli
from alpaca_bot.web.password_rotation import (
    rotate_dashboard_password,
    update_dashboard_auth_env,
)


def test_update_dashboard_auth_env_replaces_existing_values() -> None:
    original = """TRADING_MODE=paper
DASHBOARD_AUTH_ENABLED=false
DASHBOARD_AUTH_USERNAME=old@example.com
DASHBOARD_AUTH_PASSWORD_HASH='replace_me'
"""

    updated = update_dashboard_auth_env(
        original,
        username="operator@example.com",
        password_hash="scrypt$1$2$3$salt$hash",
    )

    assert "DASHBOARD_AUTH_ENABLED=true" in updated
    assert "DASHBOARD_AUTH_USERNAME=operator@example.com" in updated
    assert "DASHBOARD_AUTH_PASSWORD_HASH='scrypt$1$2$3$salt$hash'" in updated
    assert "old@example.com" not in updated


def test_update_dashboard_auth_env_appends_missing_values() -> None:
    original = "TRADING_MODE=paper\n"

    updated = update_dashboard_auth_env(
        original,
        username="operator@example.com",
        password_hash="hash-value",
    )

    assert updated.endswith(
        "DASHBOARD_AUTH_ENABLED=true\n"
        "DASHBOARD_AUTH_USERNAME=operator@example.com\n"
        "DASHBOARD_AUTH_PASSWORD_HASH=hash-value\n"
    )


def test_rotate_dashboard_password_updates_files(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    password_file = tmp_path / "dashboard_password.txt"
    env_file.write_text(
        "TRADING_MODE=paper\nDASHBOARD_AUTH_USERNAME=operator@example.com\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "alpaca_bot.web.password_rotation.generate_password",
        lambda: "generated-password",
    )
    monkeypatch.setattr(
        "alpaca_bot.web.password_rotation.hash_password",
        lambda _password: "generated-hash",
    )

    username, password = rotate_dashboard_password(
        env_file=env_file,
        password_file=password_file,
    )

    assert username == "operator@example.com"
    assert password == "generated-password"
    assert "DASHBOARD_AUTH_PASSWORD_HASH=generated-hash" in env_file.read_text(
        encoding="utf-8"
    )
    assert password_file.read_text(encoding="utf-8") == "generated-password\n"


def test_password_rotate_cli_prints_written_credentials(monkeypatch, capsys, tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    password_file = tmp_path / "dashboard_password.txt"
    env_file.write_text("TRADING_MODE=paper\n", encoding="utf-8")

    monkeypatch.setattr(
        password_rotate_cli,
        "rotate_dashboard_password",
        lambda **_kwargs: ("operator@example.com", "rotated-password"),
    )

    result = password_rotate_cli.main(
        [
            "--env-file",
            str(env_file),
            "--password-file",
            str(password_file),
            "--username",
            "operator@example.com",
        ]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert "username=operator@example.com" in captured.out
    assert "password=rotated-password" in captured.out


# ---------------------------------------------------------------------------
# rotate_dashboard_password — edge cases
# ---------------------------------------------------------------------------


def test_rotate_raises_when_username_missing_and_not_in_env(tmp_path: Path) -> None:
    import pytest

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("TRADING_MODE=paper\n", encoding="utf-8")

    with pytest.raises(ValueError, match="username"):
        rotate_dashboard_password(
            env_file=env_file,
            password_file=tmp_path / "pass.txt",
            username=None,
        )


def test_rotate_uses_provided_password_without_generating(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("DASHBOARD_AUTH_USERNAME=op@example.com\n", encoding="utf-8")

    generate_calls: list = []
    monkeypatch.setattr(
        "alpaca_bot.web.password_rotation.generate_password",
        lambda: generate_calls.append(True) or "generated",
    )
    monkeypatch.setattr("alpaca_bot.web.password_rotation.hash_password", lambda _: "hash")

    _, password = rotate_dashboard_password(
        env_file=env_file,
        password_file=tmp_path / "pass.txt",
        password="explicit-password",
    )

    assert password == "explicit-password"
    assert generate_calls == []


def test_rotate_password_file_gets_600_permissions(tmp_path: Path) -> None:
    import stat

    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("DASHBOARD_AUTH_USERNAME=op@example.com\n", encoding="utf-8")
    password_file = tmp_path / "dashboard_password.txt"

    rotate_dashboard_password(
        env_file=env_file,
        password_file=password_file,
        password="test-pass",
    )

    mode = stat.S_IMODE(password_file.stat().st_mode)
    assert mode == 0o600


# ---------------------------------------------------------------------------
# _extract_env_value — edge cases
# ---------------------------------------------------------------------------


def test_extract_env_value_returns_none_for_missing_key() -> None:
    from alpaca_bot.web.password_rotation import _extract_env_value

    assert _extract_env_value("TRADING_MODE=paper\n", "MISSING_KEY") is None


def test_extract_env_value_ignores_comment_lines() -> None:
    from alpaca_bot.web.password_rotation import _extract_env_value

    env = "# DASHBOARD_AUTH_USERNAME=comment@example.com\nTRADING_MODE=paper\n"
    assert _extract_env_value(env, "DASHBOARD_AUTH_USERNAME") is None


def test_extract_env_value_strips_single_and_double_quotes() -> None:
    from alpaca_bot.web.password_rotation import _extract_env_value

    assert _extract_env_value("KEY='quoted'\n", "KEY") == "quoted"
    assert _extract_env_value('KEY="double"\n', "KEY") == "double"


def test_extract_env_value_strips_surrounding_whitespace() -> None:
    from alpaca_bot.web.password_rotation import _extract_env_value

    assert _extract_env_value("KEY=  value  \n", "KEY") == "value"


# ---------------------------------------------------------------------------
# update_dashboard_auth_env — preservation of unrelated content
# ---------------------------------------------------------------------------


def test_update_dashboard_auth_env_preserves_comment_lines() -> None:
    original = "# This is a comment\nTRADING_MODE=paper\n"

    updated = update_dashboard_auth_env(
        original,
        username="op@example.com",
        password_hash="hash",
    )

    assert "# This is a comment" in updated


def test_update_dashboard_auth_env_preserves_non_auth_keys() -> None:
    original = "TRADING_MODE=paper\nSTRATEGY_VERSION=v1-breakout\n"

    updated = update_dashboard_auth_env(
        original,
        username="op@example.com",
        password_hash="hash",
    )

    assert "TRADING_MODE=paper" in updated
    assert "STRATEGY_VERSION=v1-breakout" in updated


def test_update_dashboard_auth_env_output_ends_with_newline() -> None:
    updated = update_dashboard_auth_env(
        "TRADING_MODE=paper\n",
        username="op@example.com",
        password_hash="hash",
    )

    assert updated.endswith("\n")

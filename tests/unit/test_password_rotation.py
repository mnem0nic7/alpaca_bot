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
        username="m7ga.77@gmail.com",
        password_hash="scrypt$1$2$3$salt$hash",
    )

    assert "DASHBOARD_AUTH_ENABLED=true" in updated
    assert "DASHBOARD_AUTH_USERNAME=m7ga.77@gmail.com" in updated
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
        lambda **_kwargs: ("m7ga.77@gmail.com", "rotated-password"),
    )

    result = password_rotate_cli.main(
        [
            "--env-file",
            str(env_file),
            "--password-file",
            str(password_file),
            "--username",
            "m7ga.77@gmail.com",
        ]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert "username=m7ga.77@gmail.com" in captured.out
    assert "password=rotated-password" in captured.out

from __future__ import annotations

import io
from pathlib import Path

from alpaca_bot.admin.credential_sync import main, sync_credential_env_file


def write_env_file(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "TRADING_MODE=paper",
                "ALPACA_PAPER_API_KEY=replace_me",
                "ALPACA_PAPER_SECRET_KEY=replace_me",
                "ALPACA_LIVE_API_KEY=replace_me",
                "ALPACA_LIVE_SECRET_KEY=replace_me",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_sync_credential_env_file_updates_provided_keys(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    write_env_file(env_file)

    updated = sync_credential_env_file(
        env_file=env_file,
        environ={
            "ALPACA_PAPER_API_KEY": "paper-key",
            "ALPACA_PAPER_SECRET_KEY": "paper-secret",
        },
    )

    assert updated == ["ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY"]
    rendered = env_file.read_text(encoding="utf-8")
    assert "ALPACA_PAPER_API_KEY=paper-key" in rendered
    assert "ALPACA_PAPER_SECRET_KEY=paper-secret" in rendered
    assert "ALPACA_LIVE_API_KEY=replace_me" in rendered


def test_sync_credential_env_file_shell_quotes_special_values(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    write_env_file(env_file)

    sync_credential_env_file(
        env_file=env_file,
        environ={
            "ALPACA_PAPER_SECRET_KEY": "two words & symbols",
        },
    )

    rendered = env_file.read_text(encoding="utf-8")
    assert "ALPACA_PAPER_SECRET_KEY='two words & symbols'" in rendered


def test_main_returns_error_when_no_credential_env_vars_are_present(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    write_env_file(env_file)
    stderr = io.StringIO()

    exit_code = main(
        ["--env-file", str(env_file)],
        environ={},
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "credential-sync failed:" in stderr.getvalue()


def test_main_prints_updated_keys_on_success(tmp_path: Path) -> None:
    env_file = tmp_path / "alpaca-bot.env"
    write_env_file(env_file)
    stdout = io.StringIO()

    exit_code = main(
        ["--env-file", str(env_file)],
        environ={"ALPACA_PAPER_API_KEY": "paper-key"},
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    assert "updated ALPACA_PAPER_API_KEY" in stdout.getvalue()

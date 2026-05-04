from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "apply_candidate.sh"


def _make_mock_deploy(tmp_path: Path) -> Path:
    """Create a mock deploy.sh that appends a sentinel line when called."""
    deploy = tmp_path / "mock_deploy.sh"
    deploy.write_text(
        "#!/usr/bin/env bash\n"
        "echo \"mock-deploy $1\" >> \"${1}.deploy_log\"\n"
    )
    deploy.chmod(deploy.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return deploy


def _run_apply(env_file: Path, candidate_env: Path, deploy_script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), str(env_file), str(candidate_env), str(deploy_script)],
        capture_output=True,
        text=True,
    )


def test_apply_no_candidate_env_exits_0_unchanged(tmp_path):
    """No candidate.env → exit 0, env file unchanged, deploy not called."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("BREAKOUT_LOOKBACK_BARS=20\nRELATIVE_VOLUME_THRESHOLD=1.5\n")
    candidate_env = tmp_path / "candidate.env"  # does NOT exist
    mock_deploy = _make_mock_deploy(tmp_path)

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    assert env_file.read_text() == "BREAKOUT_LOOKBACK_BARS=20\nRELATIVE_VOLUME_THRESHOLD=1.5\n"
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert not deploy_log.exists(), "deploy.sh must not be called when no candidate.env"


def test_apply_updates_changed_params_and_calls_deploy(tmp_path):
    """Changed params → env file updated with new values, deploy.sh called."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "TRADING_MODE=paper\n"
        "BREAKOUT_LOOKBACK_BARS=20\n"
        "RELATIVE_VOLUME_THRESHOLD=1.5\n"
        "DAILY_SMA_PERIOD=20\n"
    )
    candidate_env = tmp_path / "candidate.env"
    candidate_env.write_text(
        "# Best params from tuning run 2026-05-04T22:30:00Z\n"
        "# Score=0.84  Trades=47  WinRate=68%\n"
        "BREAKOUT_LOOKBACK_BARS=30\n"
        "RELATIVE_VOLUME_THRESHOLD=2.0\n"
        "DAILY_SMA_PERIOD=50\n"
    )
    mock_deploy = _make_mock_deploy(tmp_path)

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    updated = env_file.read_text()
    assert "BREAKOUT_LOOKBACK_BARS=30" in updated
    assert "RELATIVE_VOLUME_THRESHOLD=2.0" in updated
    assert "DAILY_SMA_PERIOD=50" in updated
    assert "TRADING_MODE=paper" in updated
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert deploy_log.exists(), "deploy.sh must be called when params changed"


def test_apply_no_op_when_params_already_current(tmp_path):
    """Params in candidate match env file → no env change, deploy.sh NOT called."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text(
        "BREAKOUT_LOOKBACK_BARS=30\n"
        "RELATIVE_VOLUME_THRESHOLD=2.0\n"
        "DAILY_SMA_PERIOD=50\n"
    )
    candidate_env = tmp_path / "candidate.env"
    candidate_env.write_text(
        "# Same params\n"
        "BREAKOUT_LOOKBACK_BARS=30\n"
        "RELATIVE_VOLUME_THRESHOLD=2.0\n"
        "DAILY_SMA_PERIOD=50\n"
    )
    mock_deploy = _make_mock_deploy(tmp_path)
    original_content = env_file.read_text()

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    assert env_file.read_text() == original_content, "env file must not change"
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert not deploy_log.exists(), "deploy.sh must NOT be called when params unchanged"


def test_apply_appends_param_not_in_env_file(tmp_path):
    """A param in candidate.env not present in env file is appended."""
    env_file = tmp_path / "alpaca-bot.env"
    env_file.write_text("TRADING_MODE=paper\nBREAKOUT_LOOKBACK_BARS=20\n")
    candidate_env = tmp_path / "candidate.env"
    candidate_env.write_text(
        "BREAKOUT_LOOKBACK_BARS=20\n"    # unchanged
        "DAILY_SMA_PERIOD=50\n"          # NEW — not in env file
    )
    mock_deploy = _make_mock_deploy(tmp_path)

    result = _run_apply(env_file, candidate_env, mock_deploy)

    assert result.returncode == 0
    updated = env_file.read_text()
    assert "DAILY_SMA_PERIOD=50" in updated
    deploy_log = Path(str(env_file) + ".deploy_log")
    assert deploy_log.exists(), "deploy.sh must be called when a new param is added"

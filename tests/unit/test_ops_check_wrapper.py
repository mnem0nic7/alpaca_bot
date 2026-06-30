from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "ops_check.sh"


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    arg_log = tmp_path / "docker_args.txt"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$DOCKER_ARG_LOG\"\n"
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir, arg_log


def _run_wrapper(tmp_path: Path, args: list[str], env_file: Path | None = None) -> subprocess.CompletedProcess:
    bin_dir, arg_log = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["DOCKER_ARG_LOG"] = str(arg_log)
    env.pop("ALPACA_BOT_ENV_FILE", None)
    if env_file is not None:
        env["ALPACA_BOT_ENV_FILE"] = str(env_file)

    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_ops_check_wrapper_consumes_positional_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / "paper.env"
    env_file.write_text("TRADING_MODE=paper\nSTRATEGY_VERSION=v1-breakout\n")

    result = _run_wrapper(
        tmp_path,
        [str(env_file), "--expect-trading-mode", "paper"],
    )

    assert result.returncode == 0, result.stderr
    docker_args = (tmp_path / "docker_args.txt").read_text().splitlines()
    assert docker_args[:3] == ["compose", "--env-file", str(env_file)]
    admin_index = docker_args.index("admin")
    assert str(env_file) not in docker_args[admin_index:]
    assert docker_args[-3:] == ["admin", "--expect-trading-mode", "paper"]


def test_ops_check_wrapper_uses_env_var_when_first_arg_is_flag(tmp_path: Path) -> None:
    env_file = tmp_path / "paper.env"
    env_file.write_text("TRADING_MODE=paper\nSTRATEGY_VERSION=v1-breakout\n")

    result = _run_wrapper(
        tmp_path,
        ["--expect-trading-mode", "paper"],
        env_file=env_file,
    )

    assert result.returncode == 0, result.stderr
    docker_args = (tmp_path / "docker_args.txt").read_text().splitlines()
    assert docker_args[:3] == ["compose", "--env-file", str(env_file)]
    assert docker_args[-3:] == ["admin", "--expect-trading-mode", "paper"]

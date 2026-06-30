from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "admin.sh"


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


def test_admin_wrapper_passes_env_file_to_compose(tmp_path: Path) -> None:
    bin_dir, arg_log = _fake_docker(tmp_path)
    env_file = tmp_path / "paper.env"
    env_file.write_text("TRADING_MODE=paper\nSTRATEGY_VERSION=v1-breakout\n")
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["DOCKER_ARG_LOG"] = str(arg_log)
    env["ALPACA_BOT_ENV_FILE"] = str(env_file)

    result = subprocess.run(
        [str(SCRIPT), "status"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    docker_args = arg_log.read_text().splitlines()
    assert docker_args[:3] == ["compose", "--env-file", str(env_file)]
    assert docker_args[-2:] == ["admin", "status"]

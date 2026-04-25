from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
from typing import Mapping, Sequence, TextIO


ALPACA_SECRET_KEYS = (
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_SECRET_KEY",
    "ALPACA_LIVE_API_KEY",
    "ALPACA_LIVE_SECRET_KEY",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpaca-bot-sync-credentials")
    parser.add_argument("--env-file", required=True)
    return parser


def sync_credential_env_file(
    *,
    env_file: str | Path,
    environ: Mapping[str, str],
) -> list[str]:
    path = Path(env_file)
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")

    original_text = path.read_text(encoding="utf-8")
    lines = original_text.splitlines()
    updated_keys: list[str] = []

    for key in ALPACA_SECRET_KEYS:
        value = environ.get(key)
        if value is None or value == "":
            continue
        quoted = f"{key}={shlex.quote(value)}"
        pattern = re.compile(rf"^{re.escape(key)}=")
        replaced = False
        for index, line in enumerate(lines):
            if pattern.match(line):
                lines[index] = quoted
                replaced = True
                break
        if not replaced:
            lines.append(quoted)
        updated_keys.append(key)

    if not updated_keys:
        raise ValueError("No Alpaca credential environment variables were provided")

    new_text = "\n".join(lines) + "\n"
    file_mode = stat.S_IMODE(path.stat().st_mode) or 0o600
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
    ) as handle:
        handle.write(new_text)
        temp_path = Path(handle.name)
    os.chmod(temp_path, file_mode)
    os.replace(temp_path, path)
    return updated_keys


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        updated_keys = sync_credential_env_file(
            env_file=args.env_file,
            environ=dict(os.environ if environ is None else environ),
        )
    except Exception as exc:
        print(f"credential-sync failed: {exc}", file=stderr or sys.stderr)
        return 1

    print(
        "updated " + ", ".join(updated_keys),
        file=stdout or sys.stdout,
    )
    return 0

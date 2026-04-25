from __future__ import annotations

from collections.abc import Sequence
import argparse

from pathlib import Path

from alpaca_bot.web.password_rotation import rotate_dashboard_password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rotate the dashboard password and update the env file."
    )
    parser.add_argument("--env-file", default="/etc/alpaca_bot/alpaca-bot.env")
    parser.add_argument("--password-file", default="/etc/alpaca_bot/dashboard_password.txt")
    parser.add_argument("--username")
    parser.add_argument("--password")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    username, password = rotate_dashboard_password(
        env_file=Path(args.env_file),
        password_file=Path(args.password_file),
        username=args.username,
        password=args.password,
    )
    print(f"username={username}")
    print(f"password={password}")
    print(f"env_file={args.env_file}")
    print(f"password_file={args.password_file}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

from __future__ import annotations

from collections.abc import Sequence
import argparse
import getpass

from alpaca_bot.web.auth import hash_password


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a dashboard password hash.")
    parser.add_argument("--password", help="Password to hash. If omitted, prompt securely.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    password = args.password if args.password is not None else getpass.getpass("Password: ")
    print(hash_password(password))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Callable, Sequence, TextIO
from urllib.error import URLError
from urllib.request import urlopen


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpaca-bot-ops-check")
    parser.add_argument("--url", default="http://web:8080/healthz")
    parser.add_argument(
        "--expect-worker",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require worker_status=fresh in the health response.",
    )
    parser.add_argument("--wait-seconds", type=float, default=30.0)
    parser.add_argument("--retry-interval-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    return parser


def run_ops_check(
    *,
    url: str,
    expect_worker: bool,
    wait_seconds: float,
    retry_interval_seconds: float,
    timeout_seconds: float,
    urlopen_fn: Callable[..., object] = urlopen,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    deadline = time.monotonic() + max(wait_seconds, 0.0)
    last_error: str | None = None

    while True:
        try:
            payload = _load_health_payload(
                url=url,
                timeout_seconds=timeout_seconds,
                urlopen_fn=urlopen_fn,
            )
            errors = _validate_health_payload(payload, expect_worker=expect_worker)
            if not errors:
                return payload
            last_error = "; ".join(errors)
        except Exception as exc:  # pragma: no cover - exercised via main
            last_error = str(exc)

        if time.monotonic() >= deadline:
            break
        sleep_fn(retry_interval_seconds)

    raise RuntimeError(last_error or "ops check failed")


def main(
    argv: Sequence[str] | None = None,
    *,
    urlopen_fn: Callable[..., object] = urlopen,
    sleep_fn: Callable[[float], None] = time.sleep,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        payload = run_ops_check(
            url=args.url,
            expect_worker=args.expect_worker,
            wait_seconds=args.wait_seconds,
            retry_interval_seconds=args.retry_interval_seconds,
            timeout_seconds=args.timeout_seconds,
            urlopen_fn=urlopen_fn,
            sleep_fn=sleep_fn,
        )
    except Exception as exc:
        print(f"ops-check failed: {exc}", file=stderr or sys.stderr)
        return 1

    summary = (
        f"status={payload.get('status')} "
        f"db={payload.get('db') or payload.get('database')} "
        f"trading_mode={payload.get('trading_mode')} "
        f"strategy_version={payload.get('strategy_version')} "
        f"trading_status={payload.get('trading_status')} "
        f"worker_status={payload.get('worker_status')}"
    )
    print(summary, file=stdout or sys.stdout)
    return 0


def _load_health_payload(
    *,
    url: str,
    timeout_seconds: float,
    urlopen_fn: Callable[..., object],
) -> dict[str, object]:
    try:
        response = urlopen_fn(url, timeout=timeout_seconds)
    except URLError as exc:
        raise RuntimeError(f"health request failed: {exc}") from exc

    close = getattr(response, "close", None)
    try:
        body = response.read()
    finally:
        if callable(close):
            close()

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"health response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("health response was not a JSON object")
    return payload


def _validate_health_payload(
    payload: dict[str, object],
    *,
    expect_worker: bool,
) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "ok":
        errors.append(f"status={payload.get('status')!r}")

    database_state = payload.get("db", payload.get("database"))
    if database_state != "ok":
        errors.append(f"db={database_state!r}")

    if expect_worker and payload.get("worker_status") != "fresh":
        errors.append(f"worker_status={payload.get('worker_status')!r}")

    return errors

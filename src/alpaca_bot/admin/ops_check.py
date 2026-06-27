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
    parser.add_argument("--expect-trading-mode")
    parser.add_argument("--expect-strategy-version")
    parser.add_argument("--expect-trading-status")
    parser.add_argument(
        "--expect-kill-switch",
        choices=("true", "false"),
        help="Require kill_switch_enabled to match this boolean.",
    )
    parser.add_argument(
        "--expect-enabled-strategy",
        action="append",
        default=[],
        help="Require this strategy flag to be enabled; repeatable.",
    )
    parser.add_argument(
        "--expect-disabled-strategy",
        action="append",
        default=[],
        help="Require this strategy flag to be disabled; repeatable.",
    )
    parser.add_argument(
        "--expect-only-enabled-strategy",
        action="append",
        default=[],
        help="Require the complete enabled strategy set to match; repeatable.",
    )
    return parser


def run_ops_check(
    *,
    url: str,
    expect_worker: bool,
    wait_seconds: float,
    retry_interval_seconds: float,
    timeout_seconds: float,
    expect_trading_mode: str | None = None,
    expect_strategy_version: str | None = None,
    expect_trading_status: str | None = None,
    expect_kill_switch: bool | None = None,
    expect_enabled_strategies: Sequence[str] = (),
    expect_disabled_strategies: Sequence[str] = (),
    expect_only_enabled_strategies: Sequence[str] | None = None,
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
            errors = _validate_health_payload(
                payload,
                expect_worker=expect_worker,
                expect_trading_mode=expect_trading_mode,
                expect_strategy_version=expect_strategy_version,
                expect_trading_status=expect_trading_status,
                expect_kill_switch=expect_kill_switch,
                expect_enabled_strategies=expect_enabled_strategies,
                expect_disabled_strategies=expect_disabled_strategies,
                expect_only_enabled_strategies=expect_only_enabled_strategies,
            )
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
            expect_trading_mode=args.expect_trading_mode,
            expect_strategy_version=args.expect_strategy_version,
            expect_trading_status=args.expect_trading_status,
            expect_kill_switch=_parse_expect_bool(args.expect_kill_switch),
            expect_enabled_strategies=args.expect_enabled_strategy,
            expect_disabled_strategies=args.expect_disabled_strategy,
            expect_only_enabled_strategies=(
                args.expect_only_enabled_strategy
                if args.expect_only_enabled_strategy
                else None
            ),
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
        f"kill_switch_enabled={payload.get('kill_switch_enabled')} "
        f"enabled_strategies={_format_enabled_strategies(payload)} "
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
    expect_trading_mode: str | None = None,
    expect_strategy_version: str | None = None,
    expect_trading_status: str | None = None,
    expect_kill_switch: bool | None = None,
    expect_enabled_strategies: Sequence[str] = (),
    expect_disabled_strategies: Sequence[str] = (),
    expect_only_enabled_strategies: Sequence[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "ok":
        errors.append(f"status={payload.get('status')!r}")

    database_state = payload.get("db", payload.get("database"))
    if database_state != "ok":
        errors.append(f"db={database_state!r}")

    if expect_worker and payload.get("worker_status") != "fresh":
        errors.append(f"worker_status={payload.get('worker_status')!r}")

    _validate_expected_value(errors, payload, "trading_mode", expect_trading_mode)
    _validate_expected_value(errors, payload, "strategy_version", expect_strategy_version)
    _validate_expected_value(errors, payload, "trading_status", expect_trading_status)

    if expect_kill_switch is not None:
        actual = payload.get("kill_switch_enabled")
        if actual != expect_kill_switch:
            errors.append(f"kill_switch_enabled={actual!r}")

    expects_strategy_flags = bool(
        expect_enabled_strategies
        or expect_disabled_strategies
        or expect_only_enabled_strategies is not None
    )
    if expects_strategy_flags:
        strategy_flags = _strategy_flags_by_name(payload.get("strategy_flags"))
        if strategy_flags is None:
            errors.append("strategy_flags=invalid")
        else:
            for name in expect_enabled_strategies:
                if strategy_flags.get(name) is not True:
                    errors.append(f"strategy_flags[{name!r}]={strategy_flags.get(name)!r}")
            for name in expect_disabled_strategies:
                if strategy_flags.get(name) is not False:
                    errors.append(f"strategy_flags[{name!r}]={strategy_flags.get(name)!r}")
            if expect_only_enabled_strategies is not None:
                expected_enabled = set(expect_only_enabled_strategies)
                actual_enabled = {
                    name for name, enabled in strategy_flags.items() if enabled
                }
                if actual_enabled != expected_enabled:
                    details: list[str] = []
                    missing = sorted(expected_enabled - actual_enabled)
                    unexpected = sorted(actual_enabled - expected_enabled)
                    if missing:
                        details.append(f"missing_enabled={','.join(missing)}")
                    if unexpected:
                        details.append(f"unexpected_enabled={','.join(unexpected)}")
                    errors.append(f"enabled_strategies={' '.join(details) or 'mismatch'}")

    return errors


def _parse_expect_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _validate_expected_value(
    errors: list[str],
    payload: dict[str, object],
    key: str,
    expected: str | None,
) -> None:
    if expected is None:
        return
    actual = payload.get(key)
    if actual != expected:
        errors.append(f"{key}={actual!r}")


def _strategy_flags_by_name(value: object) -> dict[str, bool] | None:
    if not isinstance(value, list):
        return None

    flags: dict[str, bool] = {}
    for item in value:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        enabled = item.get("enabled")
        if not isinstance(name, str) or not isinstance(enabled, bool):
            return None
        flags[name] = enabled
    return flags


def _format_enabled_strategies(payload: dict[str, object]) -> str:
    strategy_flags = _strategy_flags_by_name(payload.get("strategy_flags"))
    if strategy_flags is None:
        return "-"
    enabled = sorted(name for name, value in strategy_flags.items() if value)
    return ",".join(enabled) if enabled else "-"

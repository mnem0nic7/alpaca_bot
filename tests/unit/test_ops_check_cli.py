from __future__ import annotations

import io
import json
from urllib.error import URLError

from alpaca_bot.admin.ops_check import main, run_ops_check


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.closed = False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def close(self) -> None:
        self.closed = True


def test_run_ops_check_accepts_fresh_worker_when_expected() -> None:
    payload = {
        "status": "ok",
        "db": "ok",
        "trading_mode": "paper",
        "strategy_version": "v1-breakout",
        "trading_status": "enabled",
        "worker_status": "fresh",
    }
    seen: list[tuple[str, float]] = []

    result = run_ops_check(
        url="http://web:8080/healthz",
        expect_worker=True,
        wait_seconds=0.0,
        retry_interval_seconds=0.01,
        timeout_seconds=5.0,
        urlopen_fn=lambda url, timeout: seen.append((url, timeout)) or FakeResponse(payload),
        sleep_fn=lambda _seconds: None,
    )

    assert result == payload
    assert seen == [("http://web:8080/healthz", 5.0)]


def test_run_ops_check_allows_missing_worker_when_not_expected() -> None:
    payload = {
        "status": "ok",
        "db": "ok",
        "worker_status": "missing",
    }

    result = run_ops_check(
        url="http://web:8080/healthz",
        expect_worker=False,
        wait_seconds=0.0,
        retry_interval_seconds=0.01,
        timeout_seconds=5.0,
        urlopen_fn=lambda *_args, **_kwargs: FakeResponse(payload),
        sleep_fn=lambda _seconds: None,
    )

    assert result == payload


def test_run_ops_check_fails_when_worker_is_not_fresh_and_expected() -> None:
    payload = {
        "status": "ok",
        "db": "ok",
        "worker_status": "missing",
    }

    try:
        run_ops_check(
            url="http://web:8080/healthz",
            expect_worker=True,
            wait_seconds=0.0,
            retry_interval_seconds=0.01,
            timeout_seconds=5.0,
            urlopen_fn=lambda *_args, **_kwargs: FakeResponse(payload),
            sleep_fn=lambda _seconds: None,
        )
    except RuntimeError as exc:
        assert "worker_status='missing'" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected run_ops_check to fail")


def test_run_ops_check_accepts_expected_paper_readiness_state() -> None:
    payload = {
        "status": "ok",
        "db": "ok",
        "trading_mode": "paper",
        "strategy_version": "v1-breakout",
        "trading_status": "enabled",
        "kill_switch_enabled": False,
        "worker_status": "fresh",
        "strategy_flags": [
            {"name": "breakout", "enabled": False},
            {"name": "bull_flag", "enabled": True},
            {"name": "momentum", "enabled": False},
        ],
    }

    result = run_ops_check(
        url="http://web:8080/healthz",
        expect_worker=True,
        wait_seconds=0.0,
        retry_interval_seconds=0.01,
        timeout_seconds=5.0,
        expect_trading_mode="paper",
        expect_strategy_version="v1-breakout",
        expect_trading_status="enabled",
        expect_kill_switch=False,
        expect_only_enabled_strategies=["bull_flag"],
        urlopen_fn=lambda *_args, **_kwargs: FakeResponse(payload),
        sleep_fn=lambda _seconds: None,
    )

    assert result == payload


def test_run_ops_check_fails_when_unexpected_strategy_is_enabled() -> None:
    payload = {
        "status": "ok",
        "db": "ok",
        "trading_mode": "paper",
        "strategy_version": "v1-breakout",
        "trading_status": "enabled",
        "kill_switch_enabled": False,
        "worker_status": "fresh",
        "strategy_flags": [
            {"name": "breakout", "enabled": False},
            {"name": "bull_flag", "enabled": True},
            {"name": "momentum", "enabled": True},
        ],
    }

    try:
        run_ops_check(
            url="http://web:8080/healthz",
            expect_worker=True,
            wait_seconds=0.0,
            retry_interval_seconds=0.01,
            timeout_seconds=5.0,
            expect_trading_mode="paper",
            expect_strategy_version="v1-breakout",
            expect_trading_status="enabled",
            expect_kill_switch=False,
            expect_only_enabled_strategies=["bull_flag"],
            urlopen_fn=lambda *_args, **_kwargs: FakeResponse(payload),
            sleep_fn=lambda _seconds: None,
        )
    except RuntimeError as exc:
        assert "unexpected_enabled=momentum" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected run_ops_check to fail")


def test_main_prints_summary_on_success() -> None:
    payload = {
        "status": "ok",
        "db": "ok",
        "trading_mode": "paper",
        "strategy_version": "v1-breakout",
        "trading_status": "enabled",
        "kill_switch_enabled": False,
        "worker_status": "fresh",
        "strategy_flags": [
            {"name": "breakout", "enabled": False},
            {"name": "bull_flag", "enabled": True},
        ],
    }
    stdout = io.StringIO()

    exit_code = main(
        [
            "--url",
            "http://web:8080/healthz",
            "--expect-worker",
            "--expect-trading-mode",
            "paper",
            "--expect-strategy-version",
            "v1-breakout",
            "--expect-trading-status",
            "enabled",
            "--expect-kill-switch",
            "false",
            "--expect-only-enabled-strategy",
            "bull_flag",
        ],
        urlopen_fn=lambda *_args, **_kwargs: FakeResponse(payload),
        sleep_fn=lambda _seconds: None,
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == 0
    rendered = stdout.getvalue().strip()
    assert "status=ok" in rendered
    assert "db=ok" in rendered
    assert "kill_switch_enabled=False" in rendered
    assert "enabled_strategies=bull_flag" in rendered
    assert "worker_status=fresh" in rendered


def test_main_prints_failure_to_stderr() -> None:
    stderr = io.StringIO()

    exit_code = main(
        [
            "--url",
            "http://web:8080/healthz",
            "--expect-worker",
            "--wait-seconds",
            "0",
        ],
        urlopen_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("boom")),
        sleep_fn=lambda _seconds: None,
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "ops-check failed:" in stderr.getvalue()

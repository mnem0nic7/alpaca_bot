from __future__ import annotations

from dataclasses import dataclass
import io
import json

from alpaca_bot.config import Settings
from alpaca_bot.runtime.supervisor import SupervisorLoopReport


def make_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
            "SYMBOLS": "AAPL,MSFT,SPY",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.01",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )


@dataclass
class SupervisorStub:
    report: SupervisorLoopReport
    calls: list[dict[str, object]]

    def run_forever(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.report


def test_supervisor_cli_runs_loop_and_writes_json_summary() -> None:
    from alpaca_bot.runtime.supervisor_cli import main

    settings = make_settings()
    stdout = io.StringIO()
    calls: list[dict[str, object]] = []
    supervisor = SupervisorStub(
        report=SupervisorLoopReport(iterations=3, active_iterations=2, idle_iterations=1),
        calls=calls,
    )

    exit_code = main(
        ["--max-iterations", "3", "--poll-interval-seconds", "15.5"],
        settings=settings,
        supervisor_factory=lambda resolved_settings: (
            supervisor if resolved_settings == settings else None
        ),
        stdout=stdout,
    )

    assert exit_code == 0
    assert calls == [{"max_iterations": 3, "poll_interval_seconds": 15.5}]
    assert json.loads(stdout.getvalue()) == {
        "iterations": 3,
        "active_iterations": 2,
        "idle_iterations": 1,
    }

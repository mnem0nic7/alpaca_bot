from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.exit_diagnostics import (
    build_exit_diagnostics_report,
    format_exit_diagnostics_markdown,
)
from alpaca_bot.replay.report import ReplayTradeRecord


def _settings() -> Settings:
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "ENTRY_TIMEFRAME_MINUTES": "15",
    })


def _bar(symbol: str, ts: datetime, *, high: float, low: float, close: float) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1000,
    )


def _trade(
    symbol: str,
    *,
    entry_time: datetime,
    exit_time: datetime,
    entry: float = 100.0,
    exit_: float = 99.0,
    reason: str = "eod",
) -> ReplayTradeRecord:
    return ReplayTradeRecord(
        symbol=symbol,
        entry_price=entry,
        exit_price=exit_,
        quantity=1.0,
        entry_time=entry_time,
        exit_time=exit_time,
        exit_reason=reason,
        pnl=exit_ - entry,
        return_pct=(exit_ - entry) / entry,
    )


def _scenario(symbol: str, bars: list[Bar]) -> ReplayScenario:
    return ReplayScenario(
        name=symbol,
        symbol=symbol,
        starting_equity=100_000.0,
        daily_bars=bars,
        intraday_bars=bars,
    )


def test_exit_diagnostics_classifies_eod_loss_shapes() -> None:
    base = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    no_follow_trade = _trade(
        "AAA",
        entry_time=base,
        exit_time=base + timedelta(minutes=30),
        exit_=99.0,
    )
    gave_back_trade = _trade(
        "BBB",
        entry_time=base,
        exit_time=base + timedelta(minutes=30),
        exit_=99.0,
    )
    scenarios = [
        _scenario(
            "AAA",
            [
                _bar("AAA", base, high=100.10, low=99.40, close=100.0),
                _bar("AAA", base + timedelta(minutes=30), high=100.15, low=98.90, close=99.0),
            ],
        ),
        _scenario(
            "BBB",
            [
                _bar("BBB", base, high=103.00, low=99.50, close=101.0),
                _bar("BBB", base + timedelta(minutes=30), high=101.50, low=98.50, close=99.0),
            ],
        ),
    ]

    report = build_exit_diagnostics_report(
        scenarios=scenarios,
        trades=[no_follow_trade, gave_back_trade],
        strategy="bull_flag",
        no_follow_through_mfe_pct=0.0025,
        gave_back_mfe_pct=0.0025,
    )

    assert report.eod_losses == 2
    assert report.no_follow_through_losses == 1
    assert report.gave_back_losses == 1
    labels = {row.symbol: row.label for row in report.rows}
    assert labels == {"AAA": "no_follow_through", "BBB": "gave_back"}


def test_exit_diagnostics_markdown_renders_worst_eod_losses() -> None:
    base = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    scenario = _scenario(
        "AAA",
        [
            _bar("AAA", base, high=103.0, low=99.0, close=101.0),
            _bar("AAA", base + timedelta(minutes=30), high=101.0, low=98.0, close=99.0),
        ],
    )
    report = build_exit_diagnostics_report(
        scenarios=[scenario],
        trades=[
            _trade(
                "AAA",
                entry_time=base,
                exit_time=base + timedelta(minutes=30),
                exit_=99.0,
            )
        ],
        strategy="bull_flag",
    )

    md = format_exit_diagnostics_markdown(
        report,
        slippage_bps=2.0,
        scoring_note="Scoring mode: test.",
    )

    assert "# Exit diagnostics - bull_flag" in md
    assert "EOD losses" in md
    assert "gave_back" in md
    assert "| AAA |" in md


def _write_scenario(path: Path, symbol: str) -> None:
    base = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    bars = [
        {
            "symbol": symbol,
            "timestamp": (base + timedelta(minutes=15 * idx)).isoformat(),
            "open": 100.0,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
        }
        for idx, (high, low, close) in enumerate(
            [(101.0, 99.0, 100.0), (103.0, 98.0, 99.0)]
        )
    ]
    path.write_text(json.dumps({
        "name": symbol,
        "symbol": symbol,
        "starting_equity": 100000.0,
        "intraday_bars": bars,
        "daily_bars": bars,
    }))


def test_exit_diagnostics_cli_uses_portfolio_scoring(tmp_path, monkeypatch) -> None:
    import alpaca_bot.replay.cli as cli_module
    from alpaca_bot.replay.cli import main

    fixed = _settings()
    fake_cls = type("S", (), {"from_env": staticmethod(lambda *a, **k: fixed)})
    monkeypatch.setattr(cli_module, "Settings", fake_cls)

    _write_scenario(tmp_path / "AAA.json", "AAA")
    captured = {}
    base = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)

    def fake_portfolio_pooled_trades(scenarios, settings, strategy_name, *, on_progress=None):
        captured["max_open_positions"] = settings.max_open_positions
        captured["equities"] = [scenario.starting_equity for scenario in scenarios]
        captured["strategy"] = strategy_name
        return [
            _trade(
                "AAA",
                entry_time=base,
                exit_time=base + timedelta(minutes=15),
                exit_=99.0,
            )
        ]

    monkeypatch.setattr(
        cli_module,
        "portfolio_pooled_trades",
        fake_portfolio_pooled_trades,
    )
    out = tmp_path / "diag.md"
    json_out = tmp_path / "diag.json"

    rc = main([
        "exit-diagnostics",
        "--scenario-dir", str(tmp_path),
        "--strategy", "bull_flag",
        "--portfolio",
        "--max-open-positions", "4",
        "--starting-equity", "68991.62",
        "--slippage-bps", "2",
        "--output", str(out),
        "--json", str(json_out),
    ])

    assert rc == 0
    assert captured["max_open_positions"] == 4
    assert captured["equities"] == [68991.62]
    assert captured["strategy"] == "bull_flag"
    text = out.read_text()
    assert "cross-sectional top-K portfolio replay" in text
    assert "`max_open_positions=4`" in text
    payload = json.loads(json_out.read_text())
    assert payload["eod_losses"] == 1
    assert payload["rows"][0]["label"] == "gave_back"

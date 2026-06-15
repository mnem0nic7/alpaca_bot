import json
from pathlib import Path

from alpaca_bot.replay.cli import main


def _write_scenario(path: Path) -> None:
    # Minimal ReplayScenario JSON. load_scenario requires name + symbol +
    # daily_bars + intraday_bars, and Bar.from_dict requires a per-bar symbol
    # (verified against runner.py and domain/models.py). A single short bar
    # yields 0 trades — enough to exercise the CLI wiring.
    bars = [
        {
            "symbol": "AAA",
            "timestamp": "2026-01-02T14:30:00+00:00",
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1000,
        }
    ]
    path.write_text(
        json.dumps(
            {
                "name": "AAA_252d",
                "symbol": "AAA",
                "starting_equity": 100000.0,
                "intraday_bars": bars,
                "daily_bars": bars,
            }
        )
    )


def test_break_even_cli_writes_report(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1-breakout")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
    )
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")
    monkeypatch.setenv("SYMBOLS", "AAPL")
    scen_dir = tmp_path / "scen"
    scen_dir.mkdir()
    _write_scenario(scen_dir / "AAA_252d.json")
    out = tmp_path / "report.md"

    rc = main(
        [
            "break-even",
            "--scenario-dir", str(scen_dir),
            "--strategy", "bull_flag",
            "--slippage-ladder", "0,5",
            "--output", str(out),
        ]
    )
    assert rc == 0
    text = out.read_text()
    assert "Break-even slippage" in text
    assert "bull_flag" in text


def test_break_even_cli_empty_dir_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    monkeypatch.setenv("STRATEGY_VERSION", "v1-breakout")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
    )
    monkeypatch.setenv("MARKET_DATA_FEED", "sip")
    monkeypatch.setenv("SYMBOLS", "AAPL")
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(["break-even", "--scenario-dir", str(empty), "--strategy", "bull_flag"])
    assert rc == 1

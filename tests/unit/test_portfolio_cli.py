# tests/unit/test_portfolio_cli.py
import json
from pathlib import Path

from alpaca_bot.replay.cli import main

ENVKEYS = {
    "TRADING_MODE": "paper", "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://u:p@h:5432/d",
    "MARKET_DATA_FEED": "sip", "SYMBOLS": "AAA",
}


def _write_scenario(path: Path, symbol: str) -> None:
    bars = [{
        "symbol": symbol, "timestamp": "2026-01-02T14:30:00+00:00",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000,
    }]
    path.write_text(json.dumps({
        "name": f"{symbol}_x", "symbol": symbol, "starting_equity": 100000.0,
        "intraday_bars": bars, "daily_bars": bars,
    }))


def _set_env(monkeypatch):
    for k, v in ENVKEYS.items():
        monkeypatch.setenv(k, v)


def test_portfolio_audit_cli_writes_report(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA.json", "AAA")
    _write_scenario(scen / "BBB.json", "BBB")
    out = tmp_path / "report.md"
    rc = main([
        "portfolio-audit", "--scenario-dir", str(scen),
        "--strategy", "bull_flag", "--slippage-bps", "5",
        "--max-open-positions", "20", "--max-open-positions", "5",
        "--output", str(out),
    ])
    assert rc == 0
    text = out.read_text()
    assert "bull_flag" in text
    assert "K=20" in text and "K=5" in text


def test_portfolio_audit_cli_empty_dir_returns_1(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(["portfolio-audit", "--scenario-dir", str(empty), "--strategy", "bull_flag"])
    assert rc == 1

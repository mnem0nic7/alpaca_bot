# tests/unit/test_portfolio_cli.py
import json
from datetime import datetime, timezone
from pathlib import Path

from alpaca_bot.replay import cli as replay_cli
from alpaca_bot.replay.cli import main
from alpaca_bot.replay.report import ReplayTradeRecord

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


def _write_multi_session_scenario(path: Path, symbol: str, dates: list[str]) -> None:
    bars = [
        {
            "symbol": symbol,
            "timestamp": f"{session}T14:30:00+00:00",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000,
        }
        for session in dates
    ]
    path.write_text(json.dumps({
        "name": f"{symbol}_x",
        "symbol": symbol,
        "starting_equity": 100000.0,
        "intraday_bars": bars,
        "daily_bars": bars,
    }))


def _set_env(monkeypatch):
    for k, v in ENVKEYS.items():
        monkeypatch.setenv(k, v)


def _trade(symbol: str, exit_timestamp: str, pnl: float) -> ReplayTradeRecord:
    exit_time = datetime.fromisoformat(exit_timestamp)
    if exit_time.tzinfo is None:
        exit_time = exit_time.replace(tzinfo=timezone.utc)
    return ReplayTradeRecord(
        symbol=symbol,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=1,
        entry_time=exit_time,
        exit_time=exit_time,
        exit_reason="eod",
        pnl=pnl,
        return_pct=pnl / 100.0,
    )


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


def test_portfolio_audit_cli_writes_jsonl_per_k(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA.json", "AAA")
    _write_scenario(scen / "BBB.json", "BBB")
    out = tmp_path / "report.md"
    jsonl = tmp_path / "report.jsonl"
    jsonl.write_text("stale\n")

    rc = main([
        "portfolio-audit", "--scenario-dir", str(scen),
        "--strategy", "bull_flag", "--slippage-bps", "5",
        "--max-open-positions", "20", "--max-open-positions", "5",
        "--output", str(out),
        "--jsonl", str(jsonl),
    ])

    assert rc == 0
    lines = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert [line["max_open_positions"] for line in lines] == [20, 5]
    assert all(line["slippage_bps"] == 5 for line in lines)
    assert all(line["scenarios"] == 2 for line in lines)
    assert all(line["rows"][0]["strategy"] == "bull_flag" for line in lines)


def test_portfolio_audit_cli_overrides_starting_equity(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA.json", "AAA")
    captured_equities = []

    def fake_run_audit(*, scenarios, **kwargs):
        captured_equities.extend(s.starting_equity for s in scenarios)
        return []

    monkeypatch.setattr(replay_cli, "run_audit", fake_run_audit)
    out = tmp_path / "report.md"

    rc = main([
        "portfolio-audit", "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--starting-equity", "17247.80",
        "--output", str(out),
    ])

    assert rc == 0
    assert captured_equities == [17247.80]
    assert "Scenario starting equity override: $17,247.80." in out.read_text()


def test_portfolio_audit_cli_rejects_nonpositive_starting_equity(tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA.json", "AAA")

    rc = main([
        "portfolio-audit", "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--starting-equity", "0",
    ])

    assert rc == 1
    assert "--starting-equity must be greater than 0" in capsys.readouterr().err


def test_portfolio_audit_cli_rejects_duplicate_symbols(tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA_252d.json", "AAA")
    _write_scenario(scen / "AAA_30d.json", "AAA")
    jsonl = tmp_path / "report.jsonl"
    jsonl.write_text("stale\n")

    rc = main([
        "portfolio-audit", "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--jsonl", str(jsonl),
    ])

    assert rc == 1
    assert "duplicate scenario symbols: AAA" in capsys.readouterr().err
    assert jsonl.read_text() == "stale\n"


def test_portfolio_audit_cli_empty_dir_returns_1(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(["portfolio-audit", "--scenario-dir", str(empty), "--strategy", "bull_flag"])
    assert rc == 1


def test_proof_horizon_cli_measures_cumulative_gate(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    sessions = ["2026-01-02", "2026-01-05", "2026-01-06"]
    _write_multi_session_scenario(scen / "AAA.json", "AAA", sessions)
    _write_multi_session_scenario(scen / "BBB.json", "BBB", sessions)

    def fake_portfolio_pooled_trades(*args, **kwargs):
        return [
            _trade("AAA", "2026-01-02T20:00:00+00:00", -2.00),
            _trade("BBB", "2026-01-02T20:00:00+00:00", 1.00),
            _trade("AAA", "2026-01-05T20:00:00+00:00", 2.00),
            _trade("BBB", "2026-01-05T20:00:00+00:00", 1.00),
        ]

    monkeypatch.setattr(
        replay_cli, "portfolio_pooled_trades", fake_portfolio_pooled_trades
    )
    out = tmp_path / "proof.md"
    json_out = tmp_path / "proof.json"

    rc = main([
        "proof-horizon",
        "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--slippage-bps", "2",
        "--max-open-positions", "3",
        "--starting-equity", "17247.795",
        "--min-trades", "2",
        "--min-pnl", "0.01",
        "--output", str(out),
        "--json", str(json_out),
    ])

    assert rc == 0
    text = out.read_text()
    assert "# Proof horizon audit - bull_flag" in text
    assert "| historical starts checked | 3 |" in text
    assert "| starts that eventually reached proof gate | 2 |" in text
    assert "| starts not proven by data end | 1 |" in text
    assert "| eventual pass rate | 66.67% |" in text
    assert "| first-threshold pass rate | 50.00% |" in text
    assert "| first-threshold failures that later recovered | 1 |" in text
    assert "| median sessions to proof pass | 1 |" in text
    assert "| p90 sessions to proof pass | 2 |" in text

    payload = json.loads(json_out.read_text())
    assert payload["strategy"] == "bull_flag"
    assert payload["scenarios"] == 2
    assert payload["trades"] == 4
    assert payload["total_pnl"] == 2.0
    assert payload["historical_starts_checked"] == 3
    assert payload["starts_eventually_passed"] == 2
    assert payload["starts_not_proven_by_data_end"] == 1
    assert payload["first_threshold_failures_later_recovered"] == 1


def test_proof_horizon_cli_rejects_duplicate_symbols(tmp_path, monkeypatch, capsys):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA_252d.json", "AAA")
    _write_scenario(scen / "AAA_30d.json", "AAA")

    rc = main([
        "proof-horizon",
        "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
    ])

    assert rc == 1
    assert "duplicate scenario symbols: AAA" in capsys.readouterr().err

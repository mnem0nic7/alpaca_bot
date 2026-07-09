# tests/unit/test_portfolio_cli.py
import json
from datetime import date, datetime, timezone
from pathlib import Path

from alpaca_bot.domain.models import OptionContract
from alpaca_bot.replay import cli as replay_cli
from alpaca_bot.replay.cli import main
from alpaca_bot.replay.option_snapshots import append_option_chain_snapshot
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


def _trade(
    symbol: str,
    exit_timestamp: str,
    pnl: float,
    *,
    exit_reason: str = "eod",
) -> ReplayTradeRecord:
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
        exit_reason=exit_reason,
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


def test_option_basket_audit_samples_only_snapshot_covered_symbols(
    tmp_path, monkeypatch
):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    _write_scenario(scen / "AAA_252d.json", "AAA")
    _write_scenario(scen / "BBB_252d.json", "BBB")
    snapshot_path = append_option_chain_snapshot(
        snapshot_dir=tmp_path / "snapshots",
        cycle_at=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
        chains_by_symbol={
            "AAA": [
                OptionContract(
                    occ_symbol="AAA260117C00100000",
                    underlying="AAA",
                    option_type="call",
                    strike=100.0,
                    expiry=date(2026, 1, 17),
                    bid=1.0,
                    ask=1.2,
                    delta=0.5,
                    open_interest=100,
                )
            ]
        },
    )
    captured_symbols: list[tuple[str, ...]] = []

    def fake_portfolio_basket_pooled_trades(
        scenarios,
        settings,
        strategy_names,
        **kwargs,
    ):
        del settings, strategy_names, kwargs
        captured_symbols.append(tuple(scenario.symbol for scenario in scenarios))
        return []

    monkeypatch.setattr(
        replay_cli,
        "portfolio_basket_pooled_trades",
        fake_portfolio_basket_pooled_trades,
    )

    rc = main(
        [
            "portfolio-basket-audit",
            "--scenario-dir",
            str(scen),
            "--strategy",
            "bull_flag",
            "--strategy",
            "breakout_calls",
            "--option-chain-snapshots",
            str(snapshot_path),
            "--slippage-bps",
            "2",
            "--max-open-positions",
            "1",
            "--output",
            str(tmp_path / "report.md"),
        ]
    )

    assert rc == 0
    assert captured_symbols
    assert set(captured_symbols) == {("AAA",)}


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
    assert "`2` closed trades and `$0.01` cumulative P&L across `1` active" in text
    assert "| historical starts checked | 3 |" in text
    assert "| starts that eventually reached proof gate | 2 |" in text
    assert "| starts not proven by data end | 1 |" in text
    assert "| eventual pass rate | 66.67% |" in text
    assert "| starts reaching active-day threshold | 2 |" in text
    assert "| first-threshold pass rate | 50.00% |" in text
    assert "| first-threshold failures that later recovered | 1 |" in text
    assert "| median sessions to proof pass | 1 |" in text
    assert "| p90 sessions to proof pass | 2 |" in text

    payload = json.loads(json_out.read_text())
    assert payload["strategy"] == "bull_flag"
    assert payload["scenarios"] == 2
    assert payload["trades"] == 4
    assert payload["total_pnl"] == 2.0
    assert payload["min_active_days"] == 1
    assert payload["historical_starts_checked"] == 3
    assert payload["starts_eventually_passed"] == 2
    assert payload["starts_not_proven_by_data_end"] == 1
    assert payload["starts_reaching_min_active_days"] == 2
    assert payload["first_threshold_failures_later_recovered"] == 1


def test_proof_horizon_basket_cli_measures_basket_gate(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    sessions = ["2026-01-02", "2026-01-05", "2026-01-06"]
    _write_multi_session_scenario(scen / "AAA.json", "AAA", sessions)
    _write_multi_session_scenario(scen / "BBB.json", "BBB", sessions)
    captured: dict[str, object] = {}

    def fake_portfolio_basket_pooled_trades(
        scenarios,
        settings,
        strategy_names,
        *,
        strategy_equity_scales=None,
        option_chain_ledger=None,
        on_progress=None,
    ):
        captured["scenarios"] = len(scenarios)
        captured["max_open_positions"] = settings.max_open_positions
        captured["strategy_names"] = tuple(strategy_names)
        captured["scales"] = dict(strategy_equity_scales or {})
        captured["option_chain_ledger"] = option_chain_ledger
        return [
            _trade("AAA", "2026-01-02T20:00:00+00:00", -1.00),
            _trade("BBB", "2026-01-02T20:00:00+00:00", 2.00),
            _trade("AAA", "2026-01-05T20:00:00+00:00", 2.00),
            _trade("BBB", "2026-01-06T20:00:00+00:00", 1.00),
        ]

    monkeypatch.setattr(
        replay_cli,
        "portfolio_basket_pooled_trades",
        fake_portfolio_basket_pooled_trades,
    )
    out = tmp_path / "proof-basket.md"
    json_out = tmp_path / "proof-basket.json"

    rc = main([
        "proof-horizon-basket",
        "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--strategy", "ema_pullback",
        "--confidence-scale", "ema_pullback=0.10",
        "--slippage-bps", "2",
        "--max-open-positions", "1",
        "--min-trades", "2",
        "--min-pnl", "0.01",
        "--min-active-days", "1",
        "--output", str(out),
        "--json", str(json_out),
    ])

    assert rc == 0
    assert captured["scenarios"] == 2
    assert captured["max_open_positions"] == 1
    assert captured["strategy_names"] == ("bull_flag", "ema_pullback")
    assert captured["scales"] == {"ema_pullback": 0.10}
    assert captured["option_chain_ledger"] is None
    text = out.read_text()
    assert "# Proof horizon audit - bull_flag+ema_pullback" in text
    assert "| starts that eventually reached proof gate | 2 |" in text
    payload = json.loads(json_out.read_text())
    assert payload["strategy"] == "bull_flag+ema_pullback"
    assert payload["confidence_scales"] == {"ema_pullback": 0.10}
    assert payload["trades"] == 4
    assert payload["starts_eventually_passed"] == 2


def test_proof_horizon_basket_cli_requires_multiple_strategies(
    tmp_path, monkeypatch, capsys
):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()

    rc = main([
        "proof-horizon-basket",
        "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
    ])

    assert rc == 1
    assert (
        "proof-horizon-basket requires at least two --strategy values"
        in capsys.readouterr().err
    )


def test_proof_horizon_cli_applies_active_day_gate(tmp_path, monkeypatch):
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
        "--min-trades", "2",
        "--min-pnl", "0.01",
        "--min-active-days", "2",
        "--output", str(out),
        "--json", str(json_out),
    ])

    assert rc == 0
    text = out.read_text()
    assert "`2` closed trades and `$0.01` cumulative P&L across `2` active" in text
    assert "| starts that eventually reached proof gate | 1 |" in text
    assert "| starts not proven by data end | 2 |" in text
    assert "| eventual pass rate | 33.33% |" in text
    assert "| starts reaching trade threshold | 2 |" in text
    assert "| starts reaching active-day threshold | 1 |" in text
    assert "| first-threshold pass rate | 0.00% |" in text
    assert "| first-threshold failures that later recovered | 1 |" in text
    assert "| median sessions to proof pass | 2 |" in text

    payload = json.loads(json_out.read_text())
    assert payload["min_active_days"] == 2
    assert payload["starts_eventually_passed"] == 1
    assert payload["starts_not_proven_by_data_end"] == 2
    assert payload["starts_reaching_min_trades"] == 2
    assert payload["starts_reaching_min_active_days"] == 1
    assert payload["first_threshold_pass_rate"] == 0.0


def test_proof_horizon_cli_applies_robustness_gates(tmp_path, monkeypatch):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    sessions = ["2026-01-02", "2026-01-05", "2026-01-06"]
    _write_multi_session_scenario(scen / "AAA.json", "AAA", sessions)
    _write_multi_session_scenario(scen / "BBB.json", "BBB", sessions)

    def fake_portfolio_pooled_trades(*args, **kwargs):
        return [
            _trade(
                "AAA",
                "2026-01-02T20:00:00+00:00",
                2.00,
                exit_reason="profit_target",
            ),
            _trade("BBB", "2026-01-02T20:00:00+00:00", -1.00),
            _trade(
                "AAA",
                "2026-01-05T20:00:00+00:00",
                2.00,
                exit_reason="profit_target",
            ),
            _trade(
                "BBB",
                "2026-01-05T20:00:00+00:00",
                -1.00,
                exit_reason="stop",
            ),
            _trade(
                "AAA",
                "2026-01-06T20:00:00+00:00",
                2.00,
                exit_reason="profit_target",
            ),
            _trade(
                "BBB",
                "2026-01-06T20:00:00+00:00",
                2.00,
                exit_reason="profit_target",
            ),
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
        "--min-trades", "2",
        "--min-pnl", "0.01",
        "--min-active-days", "1",
        "--min-profit-factor", "1.20",
        "--max-single-win-pnl-share", "0.50",
        "--max-eod-loss-share", "0.50",
        "--output", str(out),
        "--json", str(json_out),
    ])

    assert rc == 0
    text = out.read_text()
    assert "profit factor >= `1.20`" in text
    assert "single-win P&L share <= `0.50`" in text
    assert "EOD loss share <= `0.50`" in text
    assert "| starts that eventually reached proof gate | 3 |" in text
    assert "| first-threshold pass rate | 33.33% |" in text
    assert "| first-threshold failures that later recovered | 2 |" in text
    assert "| first-threshold blocker counts | " in text
    assert "eod_loss_share:1" in text
    assert "profit_concentration:2" in text
    assert "| terminal blocker counts | none |" in text
    assert "| median sessions to proof pass | 2 |" in text

    payload = json.loads(json_out.read_text())
    assert payload["min_profit_factor"] == 1.2
    assert payload["max_single_win_pnl_share"] == 0.5
    assert payload["max_eod_loss_share"] == 0.5
    assert payload["first_threshold_passes"] == 1
    assert payload["first_threshold_failures_later_recovered"] == 2
    assert payload["first_threshold_blockers"] == {
        "eod_loss_share": 1,
        "profit_concentration": 2,
    }
    assert payload["terminal_blockers"] == {}


def test_proof_horizon_sweep_cli_scores_levers_against_robust_gate(
    tmp_path, monkeypatch
):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()
    sessions = ["2026-01-02", "2026-01-05", "2026-01-06"]
    _write_multi_session_scenario(scen / "AAA.json", "AAA", sessions)
    _write_multi_session_scenario(scen / "BBB.json", "BBB", sessions)

    def fake_portfolio_pooled_trades(_scenarios, settings, _strategy_name, **_kwargs):
        loss_reason = "stop" if settings.enable_giveback_exit else "eod"
        return [
            _trade(
                "AAA",
                "2026-01-02T20:00:00+00:00",
                2.00,
                exit_reason="profit_target",
            ),
            _trade(
                "BBB",
                "2026-01-02T20:00:00+00:00",
                -1.00,
                exit_reason=loss_reason,
            ),
        ]

    monkeypatch.setattr(
        replay_cli, "portfolio_pooled_trades", fake_portfolio_pooled_trades
    )
    out = tmp_path / "proof-sweep.md"
    json_out = tmp_path / "proof-sweep.json"

    rc = main([
        "proof-horizon-sweep",
        "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--min-trades", "2",
        "--min-pnl", "0.01",
        "--min-active-days", "1",
        "--max-eod-loss-share", "0.50",
        "--lever-label", "V_giveback_exit:on@0.0025,max_return=0",
        "--output", str(out),
        "--json", str(json_out),
    ])

    assert rc == 0
    text = out.read_text()
    assert "# Proof horizon sweep - bull_flag" in text
    assert "`V_giveback_exit:on@0.0025,max_return=0` | 1 | +1" in text
    assert "`baseline` | 0 | +0" in text
    assert "## Candidates Improving Proof Horizon" in text
    assert "enable_giveback_exit=True" in text

    payload = json.loads(json_out.read_text())
    labels = [row["label"] for row in payload["rows"]]
    assert labels == ["V_giveback_exit:on@0.0025,max_return=0", "baseline"]
    assert payload["rows"][0]["summary"]["starts_eventually_passed"] == 1
    assert payload["rows"][1]["summary"]["terminal_blockers"] == {
        "active_days": 2,
        "eod_loss_share": 1,
        "positive_pnl": 2,
        "sample_trades": 2,
    }


def test_proof_horizon_cli_rejects_non_positive_active_days(
    tmp_path, monkeypatch, capsys
):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()

    rc = main([
        "proof-horizon",
        "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--min-active-days", "0",
    ])

    assert rc == 1
    assert "--min-active-days must be greater than 0" in capsys.readouterr().err


def test_proof_horizon_cli_rejects_negative_robustness_thresholds(
    tmp_path, monkeypatch, capsys
):
    _set_env(monkeypatch)
    scen = tmp_path / "scen"
    scen.mkdir()

    rc = main([
        "proof-horizon",
        "--scenario-dir", str(scen),
        "--strategy", "bull_flag",
        "--min-profit-factor", "-1",
    ])

    assert rc == 1
    assert "--min-profit-factor must be non-negative" in capsys.readouterr().err


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

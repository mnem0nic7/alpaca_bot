from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.replay.cli import _format_audit_markdown
from alpaca_bot.replay.audit import StrategyAuditRow, classify_verdict, run_audit
from alpaca_bot.replay.report import ReplayTradeRecord


def make_settings() -> Settings:
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "ENTRY_TIMEFRAME_MINUTES": "15",
    })


def _trade(pnl: float, day: int = 1) -> ReplayTradeRecord:
    t = datetime(2026, 6, day, 15, 0, tzinfo=timezone.utc)
    return ReplayTradeRecord(
        symbol="AAPL", entry_price=100.0, exit_price=100.0 + pnl,
        quantity=1, entry_time=t, exit_time=t, exit_reason="eod",
        pnl=pnl, return_pct=pnl / 100.0,
    )


def test_classify_verdict_boundaries():
    assert classify_verdict(trades=3, ci=None, p_positive=None) == "insufficient-data"
    assert classify_verdict(trades=10, ci=(-5.0, -1.0), p_positive=1.0) == "negative-edge"
    assert classify_verdict(trades=10, ci=(0.5, 3.0), p_positive=0.01) == "positive-edge"
    assert classify_verdict(trades=10, ci=(-1.0, 2.0), p_positive=0.2) == "no-evidence"
    # CI above zero but p too weak -> still no-evidence
    assert classify_verdict(trades=10, ci=(0.1, 3.0), p_positive=0.06) == "no-evidence"


def test_run_audit_pools_and_computes_cost_drag():
    # Fake replay: frictionless run earns +10/trade on 6 trades (spread over
    # days), costed run earns +8/trade — cost drag must be 12.0 total.
    def fake_pooled(scenarios, settings, strategy_name):
        per_trade = 10.0 if settings.replay_slippage_bps == 0.0 else 8.0
        return [_trade(per_trade, day=d) for d in range(1, 7)]

    rows = run_audit(
        scenarios=["s1", "s2"],  # opaque to the fake
        settings=make_settings(),
        strategies=["breakout"],
        slippage_bps=5.0,
        pooled_trades_fn=fake_pooled,
    )
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, StrategyAuditRow)
    assert row.strategy == "breakout"
    assert row.trades == 6
    assert row.total_pnl == 48.0
    assert row.zero_cost_total_pnl == 60.0
    assert row.cost_drag == 12.0
    assert row.verdict == "positive-edge"  # constant +8 -> CI (8, 8), p 0.0
    assert row.win_rate == 1.0


def test_run_audit_reports_replay_phase_progress():
    def fake_pooled(scenarios, settings, strategy_name):
        per_trade = 10.0 if settings.replay_slippage_bps == 0.0 else 8.0
        return [_trade(per_trade, day=d) for d in range(1, 7)]

    progress = []

    run_audit(
        scenarios=["s1", "s2"],
        settings=make_settings(),
        strategies=["breakout"],
        slippage_bps=5.0,
        pooled_trades_fn=fake_pooled,
        on_progress=progress.append,
    )

    assert progress == [
        "breakout: costed replay complete (6 trades)",
        "breakout: frictionless replay complete (6 trades)",
        "breakout: 6 trades, verdict=positive-edge",
    ]


def test_run_audit_insufficient_data():
    def fake_pooled(scenarios, settings, strategy_name):
        return [_trade(1.0)] * 3

    rows = run_audit(
        scenarios=["s1"], settings=make_settings(),
        strategies=["breakout"], slippage_bps=5.0,
        pooled_trades_fn=fake_pooled,
    )
    assert rows[0].verdict == "insufficient-data"
    assert rows[0].ci_low is None and rows[0].p_positive is None


def test_format_audit_markdown_labels_p_value_direction():
    row = StrategyAuditRow(
        strategy="bull_flag",
        scenarios=80,
        trades=186,
        win_rate=0.672,
        profit_factor=1.30,
        total_pnl=1189.44,
        mean_trade_pnl=6.3948,
        annualized_sharpe=1.73,
        ci_low=-3.2062,
        ci_high=16.3553,
        p_positive=0.0955,
        zero_cost_total_pnl=1806.53,
        cost_drag=617.09,
        verdict="no-evidence",
    )

    out = _format_audit_markdown([row], slippage_bps=5.0)

    assert "p(mean<=0)" in out
    assert "p(edge>0)" not in out

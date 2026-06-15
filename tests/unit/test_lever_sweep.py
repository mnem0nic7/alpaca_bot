from alpaca_bot.replay.lever_sweep import LeverPoint, LeverSweepRow
from alpaca_bot.replay.audit import StrategyAuditRow


def _audit_row(strategy="bull_flag", ci_low=0.5, trades=100, verdict="no-evidence"):
    return StrategyAuditRow(
        strategy=strategy, scenarios=1, trades=trades, win_rate=0.6,
        profit_factor=1.1, total_pnl=10.0, mean_trade_pnl=0.1,
        annualized_sharpe=0.5, ci_low=ci_low, ci_high=ci_low + 1.0,
        p_positive=0.1, zero_cost_total_pnl=20.0, cost_drag=10.0,
        verdict=verdict,
    )


def test_lever_point_and_row_construct():
    point = LeverPoint(label="baseline", overrides={})
    row = LeverSweepRow(
        label=point.label, overrides=point.overrides,
        is_row=_audit_row(), oos_row=None,
    )
    assert row.label == "baseline"
    assert row.is_row.ci_low == 0.5
    assert row.oos_row is None


from datetime import datetime, timezone

from alpaca_bot.config import Settings
from alpaca_bot.replay.report import ReplayTradeRecord
from alpaca_bot.replay.lever_sweep import run_lever_sweep


def _settings():
    # Paper-mode base built from an explicit env dict — the project idiom
    # (see make_settings() in test_replay_audit.py). NEVER bare
    # Settings.from_env(): that reads ambient os.environ and is non-hermetic.
    return Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "ENTRY_TIMEFRAME_MINUTES": "15",
    })


def _trade(pnl):
    t0 = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    return ReplayTradeRecord(
        symbol="AAA", entry_price=100.0, exit_price=100.0 + pnl / 10.0,
        quantity=10, entry_time=t0, exit_time=t1, exit_reason="eod",
        pnl=pnl, return_pct=pnl / 1000.0,
    )


def _records(n, pnl):
    return [_trade(pnl) for _ in range(n)]


def test_run_lever_sweep_ranks_by_ci_low_desc():
    grid = [
        LeverPoint(label="baseline", overrides={}),
        LeverPoint(label="hi", overrides={"profit_target_r": 3.0}),
        LeverPoint(label="lo", overrides={"profit_target_r": 1.5}),
    ]

    def fake(scenarios, settings, strategy_name):
        # Tighter, higher-mean pnl => higher ci_low. Key off the override.
        if settings.profit_target_r == 3.0:
            return _records(40, 5.0)
        if settings.profit_target_r == 1.5:
            return _records(40, -5.0)
        return _records(40, 0.0)

    rows = run_lever_sweep(
        scenarios=[object()],  # opaque; fake ignores scenario contents
        base_settings=_settings(),
        strategy="bull_flag",
        grid=grid,
        slippage_bps=5.0,
        walk_forward=False,
        pooled_trades_fn=fake,
    )
    labels = [r.label for r in rows]
    assert labels == ["hi", "baseline", "lo"]
    assert all(r.oos_row is None for r in rows)


def test_run_lever_sweep_propagates_overrides():
    seen = {}

    def fake(scenarios, settings, strategy_name):
        seen[settings.replay_slippage_bps] = settings
        return _records(40, 1.0)

    grid = [LeverPoint(
        label="pt", overrides={"enable_profit_target": True, "profit_target_r": 3.0},
    )]
    run_lever_sweep(
        scenarios=[object()], base_settings=_settings(), strategy="bull_flag",
        grid=grid, slippage_bps=5.0, walk_forward=False, pooled_trades_fn=fake,
    )
    # run_audit calls the fn twice: costed (5 bps) and frictionless (0 bps).
    costed = seen[5.0]
    frictionless = seen[0.0]
    for s in (costed, frictionless):
        assert s.enable_profit_target is True
        assert s.profit_target_r == 3.0


def test_run_lever_sweep_insufficient_data_sorts_last():
    grid = [
        LeverPoint(label="good", overrides={"profit_target_r": 3.0}),
        LeverPoint(label="tiny", overrides={"profit_target_r": 1.5}),
    ]

    def fake(scenarios, settings, strategy_name):
        if settings.profit_target_r == 1.5:
            return _records(2, 1.0)  # below MIN_SAMPLES => ci None => insufficient-data
        return _records(40, 2.0)

    rows = run_lever_sweep(
        scenarios=[object()], base_settings=_settings(), strategy="bull_flag",
        grid=grid, slippage_bps=5.0, walk_forward=False, pooled_trades_fn=fake,
    )
    assert rows[0].label == "good"
    assert rows[-1].label == "tiny"
    assert rows[-1].is_row.verdict == "insufficient-data"


def test_run_lever_sweep_skips_invalid_lever_point():
    # dataclasses.replace re-runs Settings.validate(); relative_volume_threshold
    # <= 1.0 always raises ValueError regardless of baseline. The bad point must
    # be skipped (with an on_progress note), not abort the whole sweep.
    grid = [
        LeverPoint(label="ok", overrides={"profit_target_r": 3.0}),
        LeverPoint(label="bad", overrides={"relative_volume_threshold": 0.5}),
    ]
    notes: list[str] = []

    def fake(scenarios, settings, strategy_name):
        return _records(40, 1.0)

    rows = run_lever_sweep(
        scenarios=[object()], base_settings=_settings(), strategy="bull_flag",
        grid=grid, slippage_bps=5.0, walk_forward=False,
        pooled_trades_fn=fake, on_progress=notes.append,
    )
    labels = [r.label for r in rows]
    assert "bad" not in labels          # invalid point skipped, not fatal
    assert "ok" in labels               # valid points still measured
    assert any("SKIP bad" in n for n in notes)

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


from datetime import timedelta
from alpaca_bot.domain.models import Bar, ReplayScenario


def _bar(symbol, ts, price):
    return Bar(
        symbol=symbol, timestamp=ts, open=price, high=price + 1.0,
        low=price - 1.0, close=price, volume=1000,
    )


def _multiday_scenario(symbol="AAA", days=12):
    # One intraday bar per day at 15:00 UTC, plus a daily bar per day.
    intraday, daily = [], []
    base = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    for d in range(days):
        ts = base + timedelta(days=d)
        intraday.append(_bar(symbol, ts, 100.0 + d))
        daily.append(_bar(symbol, ts.replace(hour=21), 100.0 + d))
    return ReplayScenario(
        name=symbol, symbol=symbol, starting_equity=100000.0,
        daily_bars=daily, intraday_bars=intraday,
    )


def test_walk_forward_splits_disjoint_dates():
    seen_dates = []

    def fake(scenarios, settings, strategy_name):
        dates = sorted({b.timestamp.date() for s in scenarios for b in s.intraday_bars})
        seen_dates.append(dates)
        return _records(40, 1.0)

    grid = [LeverPoint(label="baseline", overrides={})]
    run_lever_sweep(
        scenarios=[_multiday_scenario()], base_settings=_settings(),
        strategy="bull_flag", grid=grid, slippage_bps=5.0,
        walk_forward=True, in_sample_ratio=0.8, daily_warmup=30,
        top_k=5, pooled_trades_fn=fake,
    )
    # First two calls are IS (costed+frictionless), last two are OOS.
    is_dates, oos_dates = set(seen_dates[0]), set(seen_dates[-1])
    assert is_dates and oos_dates
    assert is_dates.isdisjoint(oos_dates)


def test_top_k_bounds_oos_runs():
    grid = [
        LeverPoint(label="baseline", overrides={}),
        LeverPoint(label="a", overrides={"profit_target_r": 1.6}),
        LeverPoint(label="b", overrides={"profit_target_r": 1.7}),
        LeverPoint(label="c", overrides={"profit_target_r": 1.8}),
        LeverPoint(label="d", overrides={"profit_target_r": 1.9}),
    ]

    def fake(scenarios, settings, strategy_name):
        # ci_low rises with profit_target_r; baseline (2.0 default) highest.
        return _records(40, settings.profit_target_r)

    rows = run_lever_sweep(
        scenarios=[_multiday_scenario()], base_settings=_settings(),
        strategy="bull_flag", grid=grid, slippage_bps=5.0,
        walk_forward=True, top_k=2, pooled_trades_fn=fake,
    )
    with_oos = [r.label for r in rows if r.oos_row is not None]
    # top_k=2 highest-IS plus baseline (always confirmed).
    assert "baseline" in with_oos
    assert len(with_oos) <= 3
    # The two lowest-IS points must NOT have OOS rows.
    no_oos = {r.label for r in rows if r.oos_row is None}
    assert {"a", "b"} & no_oos


import pytest


def test_walk_forward_skips_short_scenario_not_fatal():
    # split_scenario raises ValueError for a scenario with <10 trading dates.
    # That split happens BEFORE the per-point guard loop, so without a guard it
    # would propagate uncaught and kill the whole sweep. A short scenario must be
    # skipped (with an on_progress note) while the valid one is still measured.
    notes: list[str] = []

    def fake(scenarios, settings, strategy_name):
        return _records(40, 1.0)

    grid = [LeverPoint(label="baseline", overrides={})]
    rows = run_lever_sweep(
        scenarios=[_multiday_scenario("OK", days=12),
                   _multiday_scenario("SHORT", days=5)],
        base_settings=_settings(), strategy="bull_flag", grid=grid,
        slippage_bps=5.0, walk_forward=True, pooled_trades_fn=fake,
        on_progress=notes.append,
    )
    assert [r.label for r in rows] == ["baseline"]   # sweep completed
    assert rows[0].oos_row is not None               # OOS still ran on survivor
    assert any("SKIP scenario 'SHORT'" in n for n in notes)


def test_walk_forward_all_short_raises_clean_error():
    # If NO scenario survives the split, raise one clear ValueError rather than
    # producing a misleading empty report.
    def fake(scenarios, settings, strategy_name):
        return _records(40, 1.0)

    grid = [LeverPoint(label="baseline", overrides={})]
    with pytest.raises(ValueError, match="No scenarios survived"):
        run_lever_sweep(
            scenarios=[_multiday_scenario("S1", days=5),
                       _multiday_scenario("S2", days=4)],
            base_settings=_settings(), strategy="bull_flag", grid=grid,
            slippage_bps=5.0, walk_forward=True, pooled_trades_fn=fake,
        )


import dataclasses as _dc
from alpaca_bot.replay.lever_sweep import (
    build_coarse_grid,
    build_ofat_grid,
    scenarios_support_regime_filter,
    scenarios_support_sector_filter,
    scenarios_support_vix_filter,
)


def test_ofat_grid_has_baseline_and_constructs_valid_settings():
    base = _settings()
    grid = build_ofat_grid(base)
    labels = [p.label for p in grid]
    assert "baseline" in labels
    # Baseline carries no overrides.
    assert next(p for p in grid if p.label == "baseline").overrides == {}
    # Every grid point yields a constructible Settings (in-range values only).
    for p in grid:
        _dc.replace(base, **p.overrides)  # must not raise
    # No grid point duplicates the baseline value of its single-field family.
    for p in grid:
        for field, val in p.overrides.items():
            if len(p.overrides) == 1:
                assert getattr(base, field) != val or p.label == "baseline"


def test_ofat_grid_covers_expected_families():
    grid = build_ofat_grid(_settings())
    labels = " ".join(p.label for p in grid)
    for token in ["A_initial_stop", "B_trail_atr", "C_trail_trigger",
                  "D_profit_target", "E_rel_vol", "G_vwap", "H_session",
                  "K_trend_exit", "L_vwap_breakdown_exit", "N_max_stop",
                  "O_loss_cap", "R_stop_limit_buffer",
                  "S_entry_stop_buffer", "T_max_close_to_entry",
                  "W_min_close_to_entry", "Y_rel_vol_lookback",
                  "Z_breakout_stop_buffer", "P_flatten",
                  "Q_no_follow_through", "V_giveback_exit",
                  "AF_early_loss_exit", "AG_entry_order_active_bars"]:
        assert token in labels
    # Family F (regime) is excluded by default unless the caller confirms the
    # scenario set can supply benchmark bars.
    assert "regime" not in labels
    assert not any("enable_regime_filter" in p.overrides for p in grid)
    assert "AC_vix" not in labels
    assert "AD_sector" not in labels
    assert not any("enable_vix_filter" in p.overrides for p in grid)
    assert not any("enable_sector_filter" in p.overrides for p in grid)


def test_regime_grid_point_is_opt_in_when_scenarios_support_it():
    grid = build_ofat_grid(_settings(), include_regime=True)
    labels = " ".join(p.label for p in grid)

    assert "F_regime:on" in labels
    assert any(p.overrides == {"enable_regime_filter": True} for p in grid)


def test_regime_support_detects_spy_or_embedded_regime_bars():
    settings = _settings()
    spy = _multiday_scenario(symbol="SPY")
    embedded = _multiday_scenario(symbol="AAA")
    embedded.regime_daily_bars = _multiday_scenario(symbol="SPY").daily_bars

    assert scenarios_support_regime_filter([spy], settings) is True
    assert scenarios_support_regime_filter([embedded], settings) is True
    assert scenarios_support_regime_filter([_multiday_scenario(symbol="AAA")], settings) is False


def test_market_context_grid_points_are_opt_in_when_scenarios_support_them():
    grid = build_ofat_grid(_settings(), include_vix=True, include_sector=True)
    labels = " ".join(p.label for p in grid)

    assert "AC_vix:on" in labels
    assert "AD_sector:on" in labels
    assert "AE_vix_sector:vix=on,sector=on" in labels
    assert any(p.overrides == {"enable_vix_filter": True} for p in grid)
    assert any(p.overrides == {"enable_sector_filter": True} for p in grid)


def test_market_context_support_detects_lanes_or_embedded_context_bars():
    settings = _settings()
    vix_lane = _multiday_scenario(symbol="VIXY")
    sector_lane = _multiday_scenario(symbol="XLK")
    embedded = _multiday_scenario(symbol="AAA")
    embedded.vix_daily_bars = _multiday_scenario(symbol="VIXY").daily_bars
    embedded.sector_daily_bars_by_etf = {
        "XLK": _multiday_scenario(symbol="XLK").daily_bars
    }

    assert scenarios_support_vix_filter([vix_lane], settings) is True
    assert scenarios_support_vix_filter([embedded], settings) is True
    assert scenarios_support_sector_filter([sector_lane], settings) is True
    assert scenarios_support_sector_filter([embedded], settings) is True
    assert scenarios_support_vix_filter([_multiday_scenario(symbol="AAA")], settings) is False
    assert scenarios_support_sector_filter([_multiday_scenario(symbol="AAA")], settings) is False


def test_failed_breakdown_grid_uses_strategy_specific_volume_and_recapture_levers():
    grid = build_ofat_grid(_settings(), strategy="failed_breakdown")
    labels = " ".join(p.label for p in grid)

    assert "I_failed_breakdown_volume" in labels
    assert "J_failed_breakdown_recapture" in labels
    assert "E_rel_vol" not in labels
    assert not any("relative_volume_threshold" in p.overrides for p in grid)
    assert any("failed_breakdown_volume_ratio" in p.overrides for p in grid)
    assert any("failed_breakdown_recapture_buffer_pct" in p.overrides for p in grid)


def test_stock_grid_includes_relative_volume_1_8_candidate():
    grid = build_ofat_grid(_settings(), strategy="bull_flag")

    assert any(
        point.label == "E_rel_vol:relative_volume_threshold=1.8"
        and point.overrides == {"relative_volume_threshold": 1.8}
        for point in grid
    )


def test_momentum_grid_includes_prior_high_lookback_lever():
    grid = build_ofat_grid(_settings(), strategy="momentum")
    labels = " ".join(p.label for p in grid)

    assert "U_prior_high_lookback" in labels
    assert any("prior_day_high_lookback_bars" in p.overrides for p in grid)
    assert any(p.overrides.get("prior_day_high_lookback_bars") == 2 for p in grid)


def test_coarse_grid_smaller_than_ofat():
    base = _settings()
    assert len(build_coarse_grid(base)) < len(build_ofat_grid(base))
    assert any(p.label == "baseline" for p in build_coarse_grid(base))


def test_coarse_grid_includes_exit_filter_levers():
    grid = build_coarse_grid(_settings())
    labels = " ".join(p.label for p in grid)

    assert "G_vwap:on" in labels
    assert "K_trend_exit:on" in labels
    assert "L_vwap_breakdown_exit:on" in labels
    assert "Q_no_follow_through:90m@0.0025" in labels
    assert "V_giveback_exit:on@0.0025,max_return=0" in labels
    assert "AF_early_loss_exit:45m@0.005" in labels
    assert "AG_entry_order_active_bars:2" in labels


def test_ema_pullback_grid_includes_ema_period_family():
    grid = build_ofat_grid(_settings(), strategy="ema_pullback")
    labels = [p.label for p in grid]

    assert "AH_ema_period:ema_period=5" in labels
    assert "AH_ema_period:ema_period=7" in labels
    assert "AH_ema_period:ema_period=12" in labels
    assert "AH_ema_period:ema_period=20" in labels
    assert "AH_ema_period:ema_period=9" not in labels
    assert any(p.overrides == {"ema_period": 7} for p in grid)
    for point in grid:
        _dc.replace(_settings(), **point.overrides)


def test_ema_pullback_coarse_grid_includes_ema_period_candidate():
    grid = build_coarse_grid(_settings(), strategy="ema_pullback")
    labels = [p.label for p in grid]

    assert "AH_ema_period:ema_period=7" in labels
    assert any(p.overrides == {"ema_period": 7} for p in grid)


def test_stock_strategy_grids_include_strategy_specific_setup_levers():
    cases = {
        "breakout": [
            ("X_breakout_lookback:breakout_lookback_bars=10", {"breakout_lookback_bars": 10}),
        ],
        "orb": [
            ("AA_orb_opening_bars:orb_opening_bars=3", {"orb_opening_bars": 3}),
        ],
        "high_watermark": [
            (
                "AB_high_watermark_lookback:high_watermark_lookback_days=126",
                {"high_watermark_lookback_days": 126},
            ),
        ],
        "vwap_reversion": [
            ("AI_vwap_dip:vwap_dip_threshold_pct=0.02", {"vwap_dip_threshold_pct": 0.02}),
        ],
        "gap_and_go": [
            ("AJ_gap_threshold:gap_threshold_pct=0.01", {"gap_threshold_pct": 0.01}),
            ("AK_gap_volume:gap_volume_threshold=1.5", {"gap_volume_threshold": 1.5}),
        ],
        "bb_squeeze": [
            ("AL_bb_period:bb_period=10", {"bb_period": 10}),
            ("AM_bb_std_dev:bb_std_dev=1.5", {"bb_std_dev": 1.5}),
            (
                "AN_bb_squeeze_threshold:bb_squeeze_threshold_pct=0.05",
                {"bb_squeeze_threshold_pct": 0.05},
            ),
            ("AO_bb_squeeze_min_bars:bb_squeeze_min_bars=3", {"bb_squeeze_min_bars": 3}),
        ],
        "bull_flag": [
            (
                "AP_bull_flag_min_run:bull_flag_min_run_pct=0.015",
                {"bull_flag_min_run_pct": 0.015},
            ),
            (
                "AQ_bull_flag_range:bull_flag_consolidation_range_pct=0.6",
                {"bull_flag_consolidation_range_pct": 0.6},
            ),
            (
                "AR_bull_flag_volume:bull_flag_consolidation_volume_ratio=0.7",
                {"bull_flag_consolidation_volume_ratio": 0.7},
            ),
        ],
    }

    for strategy, expected in cases.items():
        grid = build_ofat_grid(_settings(), strategy=strategy)
        labels = [p.label for p in grid]
        for label, overrides in expected:
            assert label in labels
            assert any(p.overrides == overrides for p in grid)
        for point in grid:
            _dc.replace(_settings(), **point.overrides)


def test_stock_strategy_coarse_grids_include_strategy_specific_setup_levers():
    cases = {
        "breakout": {"breakout_lookback_bars": 10},
        "orb": {"orb_opening_bars": 3},
        "high_watermark": {"high_watermark_lookback_days": 126},
        "vwap_reversion": {"vwap_dip_threshold_pct": 0.02},
        "gap_and_go": {"gap_threshold_pct": 0.01},
        "bb_squeeze": {"bb_squeeze_threshold_pct": 0.05},
        "bull_flag": {"bull_flag_min_run_pct": 0.015},
    }

    for strategy, expected_override in cases.items():
        grid = build_coarse_grid(_settings(), strategy=strategy)

        assert any(p.overrides == expected_override for p in grid)
        assert any("relative_volume_lookback_bars" in p.overrides for p in grid)
        assert any("breakout_stop_buffer_pct" in p.overrides for p in grid)


def test_coarse_grid_can_include_regime_filter():
    grid = build_coarse_grid(
        _settings(),
        include_regime=True,
        include_vix=True,
        include_sector=True,
    )
    labels = " ".join(p.label for p in grid)

    assert "F_regime:on" in labels
    assert "AC_vix:on" in labels
    assert "AD_sector:on" in labels
    assert "AE_vix_sector:vix=on,sector=on" in labels
    assert "N_max_stop:max_stop_pct=0.04" in labels
    assert "O_loss_cap:max_loss_per_trade_dollars=10.0" in labels
    assert "R_stop_limit_buffer:stop_limit_buffer_pct=0.00025" in labels
    assert "S_entry_stop_buffer:entry_stop_price_buffer=0.03" in labels
    assert "T_max_close_to_entry:entry_max_close_to_entry_pct=0.005" in labels
    assert "W_min_close_to_entry:entry_min_close_to_entry_pct=-0.02" in labels
    assert "Y_rel_vol_lookback:relative_volume_lookback_bars=10" in labels
    assert "Z_breakout_stop_buffer:breakout_stop_buffer_pct=0.0005" in labels
    assert "P_flatten:flatten=15:15,entry_end=15:00" in labels
    assert any("enable_trend_filter_exit" in p.overrides for p in grid)
    assert any("enable_vwap_breakdown_exit" in p.overrides for p in grid)
    assert any("enable_no_follow_through_exit" in p.overrides for p in grid)
    assert any("enable_giveback_exit" in p.overrides for p in grid)
    assert any("enable_early_loss_exit" in p.overrides for p in grid)
    assert any("entry_order_active_bars" in p.overrides for p in grid)
    assert any(p.overrides.get("enable_vwap_entry_filter") is True for p in grid)
    assert any("max_stop_pct" in p.overrides for p in grid)
    assert any("max_loss_per_trade_dollars" in p.overrides for p in grid)
    assert any("stop_limit_buffer_pct" in p.overrides for p in grid)
    assert any("entry_stop_price_buffer" in p.overrides for p in grid)
    assert any("entry_max_close_to_entry_pct" in p.overrides for p in grid)
    assert any("entry_min_close_to_entry_pct" in p.overrides for p in grid)
    assert any("relative_volume_lookback_bars" in p.overrides for p in grid)
    assert any("breakout_stop_buffer_pct" in p.overrides for p in grid)
    assert any(
        "flatten_time" in p.overrides and "entry_window_end" in p.overrides
        for p in grid
    )


def test_coarse_grid_toggles_vwap_entry_filter_from_enabled_baseline():
    base = Settings.from_env({
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
        "ENTRY_TIMEFRAME_MINUTES": "15",
        "ENABLE_VWAP_ENTRY_FILTER": "true",
    })
    grid = build_coarse_grid(base)

    assert any(
        p.label == "G_vwap:off"
        and p.overrides == {"enable_vwap_entry_filter": False}
        for p in grid
    )


def test_failed_breakdown_coarse_grid_omits_inert_relative_volume_lever():
    grid = build_coarse_grid(_settings(), strategy="failed_breakdown")
    labels = " ".join(p.label for p in grid)

    assert "I_failed_breakdown_volume" in labels
    assert "J_failed_breakdown_recapture" in labels
    assert "E_rel_vol" not in labels
    assert not any("relative_volume_threshold" in p.overrides for p in grid)


def test_momentum_coarse_grid_includes_prior_high_lookback_lever():
    grid = build_coarse_grid(_settings(), strategy="momentum")

    assert any(
        p.label == "U_prior_high_lookback:prior_day_high_lookback_bars=2"
        and p.overrides == {"prior_day_high_lookback_bars": 2}
        for p in grid
    )


from alpaca_bot.replay.lever_sweep import format_lever_sweep_markdown


def test_report_contains_baseline_and_ranking():
    rows = [
        LeverSweepRow(
            label="D_profit_target:on@3.0", overrides={"profit_target_r": 3.0},
            is_row=_audit_row(ci_low=1.2, verdict="positive-edge"),
            oos_row=_audit_row(ci_low=0.4, verdict="no-evidence"),
        ),
        LeverSweepRow(
            label="baseline", overrides={},
            is_row=_audit_row(ci_low=-0.8, verdict="no-evidence"),
            oos_row=_audit_row(ci_low=-1.0, verdict="no-evidence"),
        ),
    ]
    md = format_lever_sweep_markdown(
        rows, strategy="bull_flag", slippage_bps=5.0,
    )
    assert "# Lever sweep — bull_flag" in md
    assert "baseline" in md
    assert "D_profit_target:on@3.0" in md
    assert "Δci_low" in md or "delta" in md.lower()
    # Surviving-candidate section names the override.
    assert "profit_target_r" in md


import json as _json


def _write_scenario(tmp_path, name):
    base = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    intraday, daily = [], []
    for d in range(12):
        ts = base + timedelta(days=d)
        intraday.append({
            "symbol": name, "timestamp": ts.isoformat(), "open": 100.0 + d,
            "high": 101.0 + d, "low": 99.0 + d, "close": 100.0 + d, "volume": 1000,
        })
        daily.append({
            "symbol": name, "timestamp": ts.replace(hour=21).isoformat(),
            "open": 100.0 + d, "high": 101.0 + d, "low": 99.0 + d,
            "close": 100.0 + d, "volume": 1000,
        })
    payload = {
        "name": name, "symbol": name, "starting_equity": 100000.0,
        "daily_bars": daily, "intraday_bars": intraday,
    }
    (tmp_path / f"{name}.json").write_text(_json.dumps(payload))


def test_cli_lever_sweep_writes_report(tmp_path, monkeypatch):
    # main() calls a bare Settings.from_env() internally. Make it hermetic by
    # patching cli.Settings to return a fixed paper-mode Settings, mirroring
    # the _patch_settings idiom in test_backtest_cli.py. Do NOT depend on
    # ambient os.environ. The sweep then runs a REAL replay (no injected fake
    # pooled_trades_fn) over the two tiny scenarios — exercising the full
    # CLI -> run_lever_sweep -> run_audit -> ReplayRunner -> report path.
    import alpaca_bot.replay.cli as cli_module
    from alpaca_bot.replay.cli import main

    fixed = _settings()
    fake_cls = type("S", (), {"from_env": staticmethod(lambda *a, **k: fixed)})
    monkeypatch.setattr(cli_module, "Settings", fake_cls)

    _write_scenario(tmp_path, "AAA")
    _write_scenario(tmp_path, "BBB")
    out = tmp_path / "report.md"
    rc = main([
        "lever-sweep", "--scenario-dir", str(tmp_path),
        "--strategy", "bull_flag", "--slippage-bps", "5",
        "--coarse", "--no-walk-forward", "--output", str(out),
    ])
    assert rc == 0
    text = out.read_text()
    # Tiny scenarios likely yield zero bull_flag trades; report_from_records([])
    # returns early (win_rate=None) so the row still constructs and the
    # formatter renders the title + baseline regardless of trade count.
    assert "# Lever sweep — bull_flag" in text
    assert "baseline" in text


def test_cli_lever_sweep_portfolio_mode_uses_pooled_scoring(tmp_path, monkeypatch):
    import alpaca_bot.replay.cli as cli_module
    from alpaca_bot.replay.cli import main

    fixed = _settings()
    fake_cls = type("S", (), {"from_env": staticmethod(lambda *a, **k: fixed)})
    monkeypatch.setattr(cli_module, "Settings", fake_cls)

    _write_scenario(tmp_path, "AAA")
    _write_scenario(tmp_path, "BBB")
    captured = {}

    def fake_portfolio_pooled_trades(scenarios, settings, strategy_name, *, on_progress=None):
        captured["equities"] = [s.starting_equity for s in scenarios]
        captured["max_open_positions"] = settings.max_open_positions
        captured["strategy"] = strategy_name
        if on_progress is not None:
            on_progress("portfolio replay complete")
        return _records(40, 1.0)

    def fake_run_lever_sweep(**kwargs):
        captured["base_max_open_positions"] = kwargs["base_settings"].max_open_positions
        captured["walk_forward"] = kwargs["walk_forward"]
        captured["pooled_trades_fn"] = kwargs["pooled_trades_fn"]
        trades = kwargs["pooled_trades_fn"](
            kwargs["scenarios"],
            kwargs["base_settings"],
            kwargs["strategy"],
        )
        assert len(trades) == 40
        return [
            LeverSweepRow(
                label="baseline",
                overrides={},
                is_row=_audit_row(ci_low=0.2, trades=40),
                oos_row=None,
            )
        ]

    monkeypatch.setattr(cli_module, "portfolio_pooled_trades", fake_portfolio_pooled_trades)
    monkeypatch.setattr(cli_module, "run_lever_sweep", fake_run_lever_sweep)

    out = tmp_path / "report.md"
    rc = main([
        "lever-sweep", "--scenario-dir", str(tmp_path),
        "--strategy", "bull_flag", "--slippage-bps", "5",
        "--coarse", "--no-walk-forward",
        "--portfolio", "--max-open-positions", "4",
        "--starting-equity", "17247.80",
        "--output", str(out),
    ])

    assert rc == 0
    assert captured["base_max_open_positions"] == 4
    assert captured["max_open_positions"] == 4
    assert captured["equities"] == [17247.80, 17247.80]
    assert captured["strategy"] == "bull_flag"
    assert captured["walk_forward"] is False
    text = out.read_text()
    assert "Scoring mode: cross-sectional top-K portfolio replay" in text
    assert "`max_open_positions=4`" in text
    assert "Starting equity override: `$17,247.80`." in text


def test_cli_lever_sweep_filters_to_requested_labels(tmp_path, monkeypatch):
    import alpaca_bot.replay.cli as cli_module
    from alpaca_bot.replay.cli import main

    fixed = _settings()
    fake_cls = type("S", (), {"from_env": staticmethod(lambda *a, **k: fixed)})
    monkeypatch.setattr(cli_module, "Settings", fake_cls)

    _write_scenario(tmp_path, "AAA")
    captured = {}

    def fake_run_lever_sweep(**kwargs):
        captured["labels"] = [point.label for point in kwargs["grid"]]
        return [
            LeverSweepRow(
                label=point.label,
                overrides=point.overrides,
                is_row=_audit_row(ci_low=0.1, trades=40),
                oos_row=None,
            )
            for point in kwargs["grid"]
        ]

    monkeypatch.setattr(cli_module, "run_lever_sweep", fake_run_lever_sweep)

    out = tmp_path / "report.md"
    rc = main([
        "lever-sweep", "--scenario-dir", str(tmp_path),
        "--strategy", "momentum", "--coarse", "--no-walk-forward",
        "--lever-label", "D_profit_target:on@3.0",
        "--output", str(out),
    ])

    assert rc == 0
    assert captured["labels"] == ["baseline", "D_profit_target:on@3.0"]


def test_cli_lever_sweep_rejects_unknown_label(tmp_path, monkeypatch, capsys):
    import alpaca_bot.replay.cli as cli_module
    from alpaca_bot.replay.cli import main

    fixed = _settings()
    fake_cls = type("S", (), {"from_env": staticmethod(lambda *a, **k: fixed)})
    monkeypatch.setattr(cli_module, "Settings", fake_cls)

    _write_scenario(tmp_path, "AAA")

    def fail_if_called(**kwargs):
        raise AssertionError("run_lever_sweep should not be called")

    monkeypatch.setattr(cli_module, "run_lever_sweep", fail_if_called)

    rc = main([
        "lever-sweep", "--scenario-dir", str(tmp_path),
        "--strategy", "momentum", "--coarse",
        "--lever-label", "does_not_exist",
    ])

    assert rc == 1
    assert "Unknown lever label(s): does_not_exist" in capsys.readouterr().err


def test_cli_lever_sweep_portfolio_rejects_duplicate_symbols(tmp_path, monkeypatch, capsys):
    import alpaca_bot.replay.cli as cli_module
    from alpaca_bot.replay.cli import main

    fixed = _settings()
    fake_cls = type("S", (), {"from_env": staticmethod(lambda *a, **k: fixed)})
    monkeypatch.setattr(cli_module, "Settings", fake_cls)

    _write_scenario(tmp_path, "AAA_252d")
    duplicate = _json.loads((tmp_path / "AAA_252d.json").read_text())
    duplicate["name"] = "AAA_30d"
    duplicate["symbol"] = "AAA_252d"
    (tmp_path / "AAA_30d.json").write_text(_json.dumps(duplicate))

    rc = main([
        "lever-sweep", "--scenario-dir", str(tmp_path),
        "--strategy", "bull_flag", "--portfolio",
        "--no-walk-forward",
    ])

    assert rc == 1
    assert "duplicate scenario symbols: AAA_252D" in capsys.readouterr().err

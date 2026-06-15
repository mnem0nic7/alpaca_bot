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


import dataclasses as _dc
from alpaca_bot.replay.lever_sweep import build_ofat_grid, build_coarse_grid


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
                  "D_profit_target", "E_rel_vol", "F_regime", "G_vwap",
                  "H_session"]:
        assert token in labels


def test_coarse_grid_smaller_than_ofat():
    base = _settings()
    assert len(build_coarse_grid(base)) < len(build_ofat_grid(base))
    assert any(p.label == "baseline" for p in build_coarse_grid(base))


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

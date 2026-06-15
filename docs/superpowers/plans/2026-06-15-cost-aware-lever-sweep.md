# Cost-aware Lever Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a diagnostic harness that sweeps cost-drag/selectivity levers around the live-baseline config, ranks each by after-cost bootstrap CI lower bound (`ci_low`) using the trusted `run_audit` objective, with a built-in IS/OOS walk-forward — producing candidate parameter sets for the lead strategies (bull_flag, vwap_reversion) without touching production config.

**Architecture:** A new pure-orchestration module `replay/lever_sweep.py` (`LeverPoint`, `LeverSweepRow`, `run_lever_sweep`, `build_ofat_grid`, `build_coarse_grid`) that reuses `replay/audit.run_audit` as the objective and `replay/splitter.split_scenario` for walk-forward, plus a `lever-sweep` subcommand in `replay/cli.py`. No new production trading path; `evaluate_cycle` and the supervisor are untouched.

**Tech Stack:** Python 3, `dataclasses`, pytest with the project's fake-callables DI pattern (the `pooled_trades_fn` parameter on `run_audit` is the injection seam — no mocks).

**Spec:** `docs/superpowers/specs/2026-06-15-cost-aware-lever-sweep-design.md`

---

## File Structure

- Create: `src/alpaca_bot/replay/lever_sweep.py` — dataclasses + `run_lever_sweep` + grid builders.
- Modify: `src/alpaca_bot/replay/cli.py` — add `lever-sweep` subcommand, `_cmd_lever_sweep`, `_format_lever_sweep_markdown`.
- Create: `tests/unit/test_lever_sweep.py` — unit tests with fake `pooled_trades_fn` and synthetic scenarios.
- Output (ops, Task 7): `docs/strategy-audit/2026-06-15-lever-sweep-bull_flag.md` and `...-vwap_reversion.md`.

**Baseline:** run `pytest -q` before starting; record the passing count (the summary notes ~179 web tests + the broader suite; treat the current green total as the baseline). Every task ends green.

---

## Task 1: Result dataclasses

**Files:**
- Create: `src/alpaca_bot/replay/lever_sweep.py`
- Test: `tests/unit/test_lever_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_lever_sweep.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_lever_sweep.py::test_lever_point_and_row_construct -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alpaca_bot.replay.lever_sweep'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/alpaca_bot/replay/lever_sweep.py
"""Cost-aware lever sweep — a diagnostic over the run_audit objective.

Sweeps cost-drag / selectivity levers around a baseline Settings, one factor
at a time, ranking each grid point by after-cost bootstrap CI lower bound
(``ci_low``) — the quantity the audit verdict turns on. Optionally runs a
chronological in-sample / out-of-sample walk-forward so candidates that only
look good in-sample are flagged. Produces candidates only; promotion is a
separate, operator-gated step through the nightly OOS gate.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import time
from typing import Callable, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.audit import (
    PooledTradesFn,
    StrategyAuditRow,
    _replay_pooled_trades,
    run_audit,
)
from alpaca_bot.replay.splitter import split_scenario


@dataclass(frozen=True)
class LeverPoint:
    """One grid point: a label and the Settings field overrides to apply."""

    label: str
    overrides: dict  # Settings dataclass field name -> typed value


@dataclass(frozen=True)
class LeverSweepRow:
    """A grid point's in-sample audit row and (optionally) its OOS audit row."""

    label: str
    overrides: dict
    is_row: StrategyAuditRow
    oos_row: StrategyAuditRow | None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_lever_sweep.py::test_lever_point_and_row_construct -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/lever_sweep.py tests/unit/test_lever_sweep.py
git commit -m "feat(lever-sweep): result dataclasses for cost-aware lever sweep

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: `run_lever_sweep` core (no walk-forward) — rank by `ci_low`

**Files:**
- Modify: `src/alpaca_bot/replay/lever_sweep.py`
- Test: `tests/unit/test_lever_sweep.py`

The injection seam: `run_audit(pooled_trades_fn=...)` accepts a callable
`(scenarios, settings, strategy_name) -> list[ReplayTradeRecord]`. Tests inject a
fake that (a) records the `Settings` it receives and (b) returns canned per-trade
records keyed off a lever override, so `run_lever_sweep` is exercised end-to-end
without running a replay.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/unit/test_lever_sweep.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_lever_sweep.py -k run_lever_sweep -v`
Expected: FAIL with `ImportError: cannot import name 'run_lever_sweep'`

- [ ] **Step 3: Write the implementation**

Append to `src/alpaca_bot/replay/lever_sweep.py`:

```python
def _ci_low_key(row: StrategyAuditRow) -> float:
    """Sort key: None ci_low (insufficient-data) sorts last under reverse=True."""
    return row.ci_low if row.ci_low is not None else float("-inf")


def _audit_one(
    *,
    scenarios: Sequence[ReplayScenario],
    base_settings: Settings,
    point: "LeverPoint",
    strategy: str,
    slippage_bps: float,
    pooled_trades_fn: PooledTradesFn,
) -> StrategyAuditRow:
    settings = dataclasses.replace(base_settings, **point.overrides)
    rows = run_audit(
        scenarios=scenarios,
        settings=settings,
        strategies=[strategy],
        slippage_bps=slippage_bps,
        pooled_trades_fn=pooled_trades_fn,
    )
    return rows[0]


def run_lever_sweep(
    *,
    scenarios: Sequence[ReplayScenario],
    base_settings: Settings,
    strategy: str,
    grid: Sequence["LeverPoint"],
    slippage_bps: float = 5.0,
    walk_forward: bool = True,
    in_sample_ratio: float = 0.8,
    daily_warmup: int = 30,
    top_k: int = 5,
    pooled_trades_fn: PooledTradesFn = _replay_pooled_trades,
    on_progress: Callable[[str], None] | None = None,
) -> list["LeverSweepRow"]:
    if walk_forward:
        pairs = [
            split_scenario(
                s, in_sample_ratio=in_sample_ratio, daily_warmup=daily_warmup
            )
            for s in scenarios
        ]
        is_scenarios: list = [is_s for is_s, _ in pairs]
        oos_scenarios: list | None = [oos_s for _, oos_s in pairs]
    else:
        is_scenarios = list(scenarios)
        oos_scenarios = None

    scored: list[tuple["LeverPoint", StrategyAuditRow]] = []
    for point in grid:
        is_row = _audit_one(
            scenarios=is_scenarios, base_settings=base_settings, point=point,
            strategy=strategy, slippage_bps=slippage_bps,
            pooled_trades_fn=pooled_trades_fn,
        )
        scored.append((point, is_row))
        if on_progress is not None:
            on_progress(
                f"IS {point.label}: ci_low={is_row.ci_low} "
                f"trades={is_row.trades} verdict={is_row.verdict}"
            )

    scored.sort(key=lambda pr: _ci_low_key(pr[1]), reverse=True)

    shortlist: set[str] = set()
    if oos_scenarios is not None:
        shortlist = {point.label for point, _ in scored[:top_k]}
        shortlist.add("baseline")  # always confirm baseline OOS for reference

    result: list["LeverSweepRow"] = []
    for point, is_row in scored:
        oos_row: StrategyAuditRow | None = None
        if oos_scenarios is not None and point.label in shortlist:
            oos_row = _audit_one(
                scenarios=oos_scenarios, base_settings=base_settings, point=point,
                strategy=strategy, slippage_bps=slippage_bps,
                pooled_trades_fn=pooled_trades_fn,
            )
            if on_progress is not None:
                on_progress(
                    f"OOS {point.label}: ci_low={oos_row.ci_low} "
                    f"trades={oos_row.trades} verdict={oos_row.verdict}"
                )
        result.append(
            LeverSweepRow(
                label=point.label, overrides=point.overrides,
                is_row=is_row, oos_row=oos_row,
            )
        )
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_lever_sweep.py -k run_lever_sweep -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/lever_sweep.py tests/unit/test_lever_sweep.py
git commit -m "feat(lever-sweep): run_lever_sweep ranks grid points by after-cost ci_low

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: Walk-forward IS/OOS split + top-K OOS confirm

**Files:**
- Test: `tests/unit/test_lever_sweep.py` (no new source — exercises the `walk_forward=True` path already written in Task 2)

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/unit/test_lever_sweep.py
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
```

- [ ] **Step 2: Run tests to verify they fail, then pass**

Run: `pytest tests/unit/test_lever_sweep.py -k "walk_forward or top_k" -v`
Expected: PASS immediately (the implementation from Task 2 already covers this; if either fails, fix `run_lever_sweep`, not the test). The `Bar(symbol, timestamp, open, high, low, close, volume)` and `ReplayScenario(name, symbol, starting_equity, daily_bars, intraday_bars)` constructors used by the fixture are confirmed against `src/alpaca_bot/domain/models.py` (all positional/kwargs match; no defaults to satisfy). The 12-day fixture clears `split_scenario`'s ≥10-trading-date minimum.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_lever_sweep.py
git commit -m "test(lever-sweep): walk-forward split disjoint + top-k OOS bound

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: OFAT and coarse grid builders

**Files:**
- Modify: `src/alpaca_bot/replay/lever_sweep.py`
- Test: `tests/unit/test_lever_sweep.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/unit/test_lever_sweep.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_lever_sweep.py -k grid -v`
Expected: FAIL with `ImportError: cannot import name 'build_ofat_grid'`

- [ ] **Step 3: Write the implementation**

Append to `src/alpaca_bot/replay/lever_sweep.py`:

```python
# Single-field families: (label_prefix, settings_field, candidate_values).
# Values that equal the baseline are skipped (baseline is its own point).
_SINGLE_FIELD_FAMILIES: tuple[tuple[str, str, tuple[float, ...]], ...] = (
    ("A_initial_stop", "atr_stop_multiplier", (0.75, 1.0, 1.5, 2.0)),
    ("B_trail_atr", "trailing_stop_atr_multiplier", (0.0, 1.0, 1.5, 2.5, 3.5)),
    ("C_trail_trigger", "trailing_stop_profit_trigger_r", (0.5, 1.0, 1.5, 2.0)),
    ("E_rel_vol", "relative_volume_threshold", (1.5, 2.0, 2.5, 3.0)),
)

_PROFIT_TARGET_RS: tuple[float, ...] = (1.5, 2.0, 3.0, 4.0)
# entry_window_end values: must be > entry_window_start (10:00) and
# < flatten_time (15:45). These restrict entries to earlier windows.
_SESSION_ENDS: tuple[time, ...] = (time(12, 0), time(14, 0))


def build_ofat_grid(base_settings: Settings) -> list[LeverPoint]:
    """One-factor-at-a-time grid around the baseline. ~22 points."""
    points: list[LeverPoint] = [LeverPoint(label="baseline", overrides={})]

    for prefix, field, values in _SINGLE_FIELD_FAMILIES:
        base_val = getattr(base_settings, field)
        for v in values:
            if v == base_val:
                continue  # already the baseline point
            points.append(
                LeverPoint(label=f"{prefix}:{field}={v}", overrides={field: v})
            )

    # Family D — fixed profit target (two coupled fields).
    for r in _PROFIT_TARGET_RS:
        points.append(
            LeverPoint(
                label=f"D_profit_target:on@{r}",
                overrides={"enable_profit_target": True, "profit_target_r": r},
            )
        )

    # Family F — regime filter (toggle opposite of baseline).
    regime_target = not base_settings.enable_regime_filter
    points.append(
        LeverPoint(
            label=f"F_regime:{'on' if regime_target else 'off'}",
            overrides={"enable_regime_filter": regime_target},
        )
    )

    # Family G — VWAP entry filter (toggle opposite of baseline).
    vwap_target = not base_settings.enable_vwap_entry_filter
    points.append(
        LeverPoint(
            label=f"G_vwap:{'on' if vwap_target else 'off'}",
            overrides={"enable_vwap_entry_filter": vwap_target},
        )
    )

    # Family H — session restriction (earlier entry_window_end).
    for end in _SESSION_ENDS:
        if end == base_settings.entry_window_end:
            continue
        points.append(
            LeverPoint(
                label=f"H_session:end={end.strftime('%H:%M')}",
                overrides={"entry_window_end": end},
            )
        )

    return points


def build_coarse_grid(base_settings: Settings) -> list[LeverPoint]:
    """Reduced grid (one hypothesised-best value per family) for a fast pass."""
    points: list[LeverPoint] = [LeverPoint(label="baseline", overrides={})]
    coarse: tuple[tuple[str, dict], ...] = (
        ("A_initial_stop:atr_stop_multiplier=1.5", {"atr_stop_multiplier": 1.5}),
        ("B_trail_atr:trailing_stop_atr_multiplier=2.5",
         {"trailing_stop_atr_multiplier": 2.5}),
        ("C_trail_trigger:trailing_stop_profit_trigger_r=1.5",
         {"trailing_stop_profit_trigger_r": 1.5}),
        ("D_profit_target:on@3.0",
         {"enable_profit_target": True, "profit_target_r": 3.0}),
        ("E_rel_vol:relative_volume_threshold=2.0",
         {"relative_volume_threshold": 2.0}),
        ("F_regime:on", {"enable_regime_filter": True}),
        ("G_vwap:off", {"enable_vwap_entry_filter": False}),
        ("H_session:end=14:00", {"entry_window_end": time(14, 0)}),
    )
    for label, overrides in coarse:
        points.append(LeverPoint(label=label, overrides=overrides))
    return points
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_lever_sweep.py -k grid -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/lever_sweep.py tests/unit/test_lever_sweep.py
git commit -m "feat(lever-sweep): OFAT and coarse grid builders around live baseline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: Markdown report formatter

**Files:**
- Modify: `src/alpaca_bot/replay/lever_sweep.py`
- Test: `tests/unit/test_lever_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_lever_sweep.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_lever_sweep.py -k report -v`
Expected: FAIL with `ImportError: cannot import name 'format_lever_sweep_markdown'`

- [ ] **Step 3: Write the implementation**

Append to `src/alpaca_bot/replay/lever_sweep.py`:

```python
def _fmt(v: float | None, spec: str = ".4f") -> str:
    return "n/a" if v is None else format(v, spec)


def format_lever_sweep_markdown(
    rows: Sequence["LeverSweepRow"],
    *,
    strategy: str,
    slippage_bps: float,
    baseline_label: str = "baseline",
) -> str:
    base = next((r for r in rows if r.label == baseline_label), None)
    base_ci = base.is_row.ci_low if base and base.is_row.ci_low is not None else None

    lines: list[str] = [
        f"# Lever sweep — {strategy} ({slippage_bps:g} bps/side)",
        "",
        "Ranked by in-sample after-cost `ci_low` (the audit verdict turns on "
        "`ci_low > 0`). Read `trades` alongside `ci_low`: fewer trades widen the "
        "CI, so a high mean with few trades can still fail the verdict. "
        "Candidates only — promotion is via the nightly OOS gate.",
        "",
    ]

    if base is not None:
        lines += [
            f"**Baseline** (`{baseline_label}`): IS ci_low="
            f"{_fmt(base.is_row.ci_low)} trades={base.is_row.trades} "
            f"verdict={base.is_row.verdict}"
            + (
                f"; OOS ci_low={_fmt(base.oos_row.ci_low)} "
                f"verdict={base.oos_row.verdict}"
                if base.oos_row is not None
                else ""
            ),
            "",
        ]

    lines += [
        "| rank | lever | IS ci_low | Δci_low | IS mean | IS trades | IS p | "
        "IS verdict | OOS ci_low | OOS verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rank, r in enumerate(rows, 1):
        delta = (
            _fmt(r.is_row.ci_low - base_ci)
            if (base_ci is not None and r.is_row.ci_low is not None)
            else "n/a"
        )
        oos_ci = _fmt(r.oos_row.ci_low) if r.oos_row is not None else "—"
        oos_v = r.oos_row.verdict if r.oos_row is not None else "—"
        lines.append(
            f"| {rank} | {r.label} | {_fmt(r.is_row.ci_low)} | {delta} | "
            f"{_fmt(r.is_row.mean_trade_pnl)} | {r.is_row.trades} | "
            f"{_fmt(r.is_row.p_positive)} | {r.is_row.verdict} | "
            f"{oos_ci} | {oos_v} |"
        )

    # Surviving candidates: IS edge that holds up OOS (non-negative, not
    # negative-edge). These are the hand-off to the nightly OOS gate.
    survivors = [
        r for r in rows
        if r.oos_row is not None
        and r.oos_row.verdict != "negative-edge"
        and r.oos_row.ci_low is not None
        and r.oos_row.ci_low >= 0.0
        and r.label != baseline_label
    ]
    lines += ["", "## Candidates surviving OOS", ""]
    if not survivors:
        lines.append(
            "None. No lever point held a non-negative OOS `ci_low`. This is a "
            "valid null result — record it and iterate; do not promote anything."
        )
    else:
        for r in survivors:
            ov = ", ".join(f"{k}={v}" for k, v in r.overrides.items())
            lines.append(
                f"- `{r.label}` — overrides: {ov} — IS ci_low="
                f"{_fmt(r.is_row.ci_low)} ({r.is_row.verdict}), OOS ci_low="
                f"{_fmt(r.oos_row.ci_low)} ({r.oos_row.verdict}). "
                "Route through `alpaca-bot-nightly` (sub-project B); do not "
                "hand-apply."
            )

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_lever_sweep.py -k report -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/lever_sweep.py tests/unit/test_lever_sweep.py
git commit -m "feat(lever-sweep): markdown findings report with OOS-survivor hand-off

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: CLI subcommand `lever-sweep`

**Files:**
- Modify: `src/alpaca_bot/replay/cli.py`
- Test: `tests/unit/test_lever_sweep.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_lever_sweep.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_lever_sweep.py -k cli -v`
Expected: FAIL — argparse exits non-zero / `invalid choice: 'lever-sweep'`.

- [ ] **Step 3: Implement the subcommand**

In `src/alpaca_bot/replay/cli.py`, add the import near the top (after the existing audit import on line 13):

```python
from alpaca_bot.replay.lever_sweep import (
    build_coarse_grid,
    build_ofat_grid,
    format_lever_sweep_markdown,
    run_lever_sweep,
)
```

Add the subparser block immediately after the audit subparser (after line 94, before `args = parser.parse_args(argv)`):

```python
    # --- lever-sweep subcommand ---
    lev_p = subparsers.add_parser(
        "lever-sweep",
        help="Sweep cost-drag/selectivity levers; rank by after-cost ci_low",
    )
    lev_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    lev_p.add_argument(
        "--strategy", choices=list(STRATEGY_REGISTRY), required=True,
        help="strategy to sweep (bull_flag / vwap_reversion are the leads)",
    )
    lev_p.add_argument(
        "--slippage-bps", type=float, default=None, metavar="BPS",
        help="cost level (default: REPLAY_SLIPPAGE_BPS)",
    )
    lev_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="use only the first N scenario files (0 = all)",
    )
    lev_p.add_argument(
        "--coarse", action="store_true",
        help="reduced grid (one value per family) for a fast pass",
    )
    lev_p.add_argument(
        "--no-walk-forward", dest="walk_forward", action="store_false",
        help="skip the IS/OOS split (audit the full scenarios in-sample only)",
    )
    lev_p.add_argument("--top-k", type=int, default=5, metavar="K")
    lev_p.add_argument("--output", metavar="FILE", default="-")
```

Add the dispatch line in `main` (after the `audit` dispatch, line 105):

```python
    if args.subcommand == "lever-sweep":
        return _cmd_lever_sweep(args)
```

Add the command function (place it after `_cmd_audit`, before `_format_audit_markdown`):

```python
def _cmd_lever_sweep(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    paths = sorted(Path(args.scenario_dir).glob("*.json"))
    if args.limit > 0:
        paths = paths[: args.limit]
    if not paths:
        print(f"No scenario files in {args.scenario_dir}", file=sys.stderr)
        return 1
    scenarios = [ReplayRunner.load_scenario(p) for p in paths]

    bps = (
        args.slippage_bps
        if args.slippage_bps is not None
        else settings.replay_slippage_bps
    )
    grid = (
        build_coarse_grid(settings) if args.coarse else build_ofat_grid(settings)
    )

    rows = run_lever_sweep(
        scenarios=scenarios,
        base_settings=settings,
        strategy=args.strategy,
        grid=grid,
        slippage_bps=bps,
        walk_forward=args.walk_forward,
        top_k=args.top_k,
        on_progress=lambda msg: print(f"[lever-sweep] {msg}", file=sys.stderr),
    )

    _write_output(
        format_lever_sweep_markdown(rows, strategy=args.strategy, slippage_bps=bps),
        args.output,
    )
    return 0
```

- [ ] **Step 4: Run test to verify it passes, then the full suite**

Run: `pytest tests/unit/test_lever_sweep.py -v`
Expected: PASS (all tests in the file)
Run: `pytest -q`
Expected: PASS — baseline count + the new lever-sweep tests; zero regressions.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/cli.py tests/unit/test_lever_sweep.py
git commit -m "feat(lever-sweep): alpaca-bot-backtest lever-sweep subcommand

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: Execute the sweep and write findings (ops — gated on Tasks 1–6 green)

This task runs the harness over the real 999-scenario store and writes the
findings reports. It changes **no** production config; the bot stays in
`close_only`; `TRADING_MODE=paper` and `ENABLE_LIVE_TRADING=false` are untouched.

**Preconditions:** Tasks 1–6 committed and `pytest -q` green. The image must
contain the new code — rebuild before running in-container.

- [ ] **Step 1: Build the image with the new subcommand**

```bash
set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a
docker compose -f deploy/compose.yaml build web
```

(Any service sharing the `alpaca-bot:latest` image is fine; `web` builds it.)

- [ ] **Step 2: Coarse pass on a subset (fast directional signal), bull_flag**

```bash
docker compose -f deploy/compose.yaml run --rm \
  web alpaca-bot-backtest lever-sweep \
  --scenario-dir /data/scenarios --strategy bull_flag \
  --slippage-bps 5 --coarse --limit 200 \
  --output /data/scenarios/../lever-sweep-coarse-bull_flag.md
```

Run in the background (heavy; host shares nightly containers). Read the coarse
report to confirm the harness behaves and to spot the most promising families.

- [ ] **Step 3: Full OFAT pass with walk-forward, bull_flag (lead)**

```bash
docker compose -f deploy/compose.yaml run --rm \
  web alpaca-bot-backtest lever-sweep \
  --scenario-dir /data/scenarios --strategy bull_flag \
  --slippage-bps 5 \
  --output /var/lib/alpaca-bot/nightly/lever-sweep-bull_flag-2026-06-15.md
```

- [ ] **Step 4: Full OFAT pass with walk-forward, vwap_reversion (secondary)**

```bash
docker compose -f deploy/compose.yaml run --rm \
  web alpaca-bot-backtest lever-sweep \
  --scenario-dir /data/scenarios --strategy vwap_reversion \
  --slippage-bps 5 \
  --output /var/lib/alpaca-bot/nightly/lever-sweep-vwap_reversion-2026-06-15.md
```

- [ ] **Step 5: Copy the reports into the repo and write the analysis**

Copy both `/var/lib/alpaca-bot/nightly/lever-sweep-*-2026-06-15.md` into
`docs/strategy-audit/2026-06-15-lever-sweep-bull_flag.md` and
`...-vwap_reversion.md`. Add a short top-matter analysis to each: which lever
family moved `ci_low` most, whether any point reached positive-edge in-sample
and survived OOS, and the explicit candidate hand-off (exact Settings overrides)
or a documented null result. Do not overstate a null result.

- [ ] **Step 6: Commit the findings**

```bash
git add docs/strategy-audit/2026-06-15-lever-sweep-*.md
git commit -m "docs(lever-sweep): bull_flag + vwap_reversion findings (candidates only)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** §2 objective (`ci_low`) → Task 2 ranking. §2.1 power tension →
report note (Task 5). §3 non-levers → grid excludes sizing/timeframe (Task 4).
§4.1 module → Tasks 1–5. §4.2 CLI → Task 6. §4.3 two-stage → `--coarse` (Task 4)
+ Task 7 protocol. §5 grid → Task 4. §6 walk-forward → Tasks 2–3. §7 gating →
report hand-off text + Task 7 no-config-change. §8 report → Task 5. §9 testing →
tests across Tasks 1–6. §10/§11 constraints/hand-off → Task 7 wording.

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `LeverPoint(label, overrides)`, `LeverSweepRow(label,
overrides, is_row, oos_row)`, `run_lever_sweep(...) -> list[LeverSweepRow]`,
`build_ofat_grid/ build_coarse_grid(base_settings) -> list[LeverPoint]`,
`format_lever_sweep_markdown(rows, *, strategy, slippage_bps)` — names match
across all tasks and the CLI wiring. `StrategyAuditRow` fields used in fakes
match `replay/audit.py`. `ReplayTradeRecord` constructor matches
`replay/report.py`.

**Grilling resolutions (all verified against source, 2026-06-15):**
- `Bar` and `ReplayScenario` constructors in the Task 3 fixtures are confirmed
  against `src/alpaca_bot/domain/models.py` — exact field match, no defaults.
- **Settings construction is hermetic.** Every test helper builds Settings via
  `Settings.from_env({...explicit dict...})` (the `make_settings()` idiom in
  `test_replay_audit.py`), never bare `from_env()`. The CLI test patches
  `cli.Settings` (the `_patch_settings` idiom in `test_backtest_cli.py`) so
  `main()`'s internal bare `from_env()` is also hermetic.
- `run_audit` derives all statistics from `record.pnl` (audit.py:92) and calls
  `report_from_records`, which returns early on an empty trade list
  (report.py:66-79, `win_rate=None`) — so the CLI test's real replay yielding
  zero trades is safe.
- `load_scenario` is a `@staticmethod` (`ReplayRunner.load_scenario(p)`) reading
  top-level `name`/`symbol`/`starting_equity`(opt)/`daily_bars`/`intraday_bars`
  with `Bar.from_dict` per bar — the Task 6 fixture JSON matches.
- This is an **offline diagnostic**: no order submission, no Settings
  persistence, no Postgres/broker I/O, no AuditEvent, `evaluate_cycle` untouched,
  paper/live gates untouched. All financial-safety grill questions resolve to
  "not applicable." The only production-touching step (Task 7) runs in an
  ephemeral compose container and changes no config.

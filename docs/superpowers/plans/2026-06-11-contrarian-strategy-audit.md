# Contrarian Strategy Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the replay/sweep pipeline cost-aware and significance-aware, then audit all 11 equity strategies (plus live-only option strategies) and publish an evidence-backed verdict report.

**Architecture:** A `replay_slippage_bps` Settings knob applied adversely at the four fill sites in `ReplayRunner` (sweep/nightly inherit it for free); a pure bootstrap-stats module; a new `alpaca-bot-backtest audit` subcommand that pools per-trade P&L across a scenario directory at 0 bps vs N bps and classifies each strategy; finally an operator/analysis task that runs the audit over the 999 fresh scenarios and writes the contrarian evaluation report.

**Tech Stack:** Python 3 stdlib only (`random.Random` bootstrap, `argparse`, `dataclasses`). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-11-contrarian-strategy-audit-design.md`
**Baseline:** 1,954 passing tests. Run `pytest` before every commit.

**File map:**

| File | Role |
|---|---|
| `src/alpaca_bot/config/__init__.py` | new `replay_slippage_bps` field + parse + validation |
| `src/alpaca_bot/replay/runner.py` | `_slipped()` helper, applied at 4 fill sites |
| `src/alpaca_bot/replay/stats.py` (new) | bootstrap CI + p-value, pure functions |
| `src/alpaca_bot/replay/audit.py` (new) | `run_audit()`, `classify_verdict()`, `StrategyAuditRow` |
| `src/alpaca_bot/replay/cli.py` | `audit` subcommand wiring + markdown/JSON formatting |
| `tests/unit/test_config.py` | settings test |
| `tests/unit/test_replay_slippage.py` (new) | helper math + directional end-to-end |
| `tests/unit/test_replay_stats.py` (new) | bootstrap tests |
| `tests/unit/test_replay_audit.py` (new) | aggregation/verdict tests (fake pooled-trades callable) |
| `tests/unit/test_backtest_cli.py` | audit subcommand test |
| existing replay/tuning test fixtures | pin `REPLAY_SLIPPAGE_BPS=0` to preserve frictionless golden expectations |
| `docs/strategy-audit/2026-06-11-contrarian-strategy-evaluation.md` (new) | the analysis deliverable |

---

### Task 1: `Settings.replay_slippage_bps`

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py` (field near line 159, parse near line 379, validate near line 627)
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_config.py`, reuse its `_base_env` helper)

```python
def test_replay_slippage_bps_default_and_validation():
    settings = Settings.from_env(_base_env())
    assert settings.replay_slippage_bps == 5.0
    with pytest.raises(ValueError, match="REPLAY_SLIPPAGE_BPS"):
        Settings.from_env(_base_env(REPLAY_SLIPPAGE_BPS="-1"))
    with pytest.raises(ValueError, match="REPLAY_SLIPPAGE_BPS"):
        Settings.from_env(_base_env(REPLAY_SLIPPAGE_BPS="101"))


def test_replay_slippage_bps_env_override():
    settings = Settings.from_env(_base_env(REPLAY_SLIPPAGE_BPS="0"))
    assert settings.replay_slippage_bps == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_config.py -k replay_slippage -v`
Expected: FAIL (`TypeError`/`AttributeError`: unexpected field / no attribute `replay_slippage_bps`)

- [ ] **Step 3: Implement**

In `src/alpaca_bot/config/__init__.py`:

After `profit_target_r: float = 2.0` (line ~159) add:

```python
    # Adverse per-side slippage applied to every simulated replay fill, in
    # basis points. Sweep and nightly inherit it via the shared ReplayRunner.
    replay_slippage_bps: float = 5.0
```

In `from_env()`, next to `profit_target_r=...` (line ~379) add:

```python
            replay_slippage_bps=float(values.get("REPLAY_SLIPPAGE_BPS", "5.0")),
```

In `validate()`, after the `PROFIT_TARGET_R` check (line ~628) add:

```python
        if not 0.0 <= self.replay_slippage_bps <= 100.0:
            raise ValueError("REPLAY_SLIPPAGE_BPS must be between 0 and 100")
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/config/__init__.py tests/unit/test_config.py
git commit -m "feat: add REPLAY_SLIPPAGE_BPS setting (default 5 bps per side)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Slippage at the four fill sites in `ReplayRunner`

**Files:**
- Modify: `src/alpaca_bot/replay/runner.py` (`_process_existing_order` ~169, `_process_stop_hit` ~252, `_process_profit_target_hit` ~287, `_handle_eod_exit` ~323)
- Modify (pin frictionless fixtures): every test module whose Settings dict lacks `REPLAY_SLIPPAGE_BPS` — find with
  `grep -rln "Settings.from_env" tests/unit/test_replay*.py tests/unit/test_tuning*.py tests/unit/test_backtest_cli.py tests/unit/test_session_eval.py tests/unit/test_nightly*.py tests/unit/test_backfill_fetcher.py`
  (known: `test_replay_golden.py:15`, `test_replay_strategies.py:12`, `test_replay_runner_engine_delegation.py:30`)
- Test: `tests/unit/test_replay_slippage.py` (new)

- [ ] **Step 1: Pin existing fixtures to 0 bps**

In each `make_settings`-style base dict found by the grep above, add the line:

```python
        "REPLAY_SLIPPAGE_BPS": "0",
```

This preserves every existing exact-arithmetic expectation (golden replays, sweep scores) while production defaults to 5 bps.

- [ ] **Step 2: Write the failing tests** (`tests/unit/test_replay_slippage.py`)

```python
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"

# Same base env as tests/unit/test_replay_golden.py make_settings, so the
# golden scenario produces the same trades here.
BASE_ENV = {
    "TRADING_MODE": "paper",
    "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
    "MARKET_DATA_FEED": "sip",
    "SYMBOLS": "AAPL,MSFT,SPY",
    "DAILY_SMA_PERIOD": "20",
    "BREAKOUT_LOOKBACK_BARS": "20",
    "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
    "RELATIVE_VOLUME_THRESHOLD": "1.5",
    "ENTRY_TIMEFRAME_MINUTES": "15",
    "RISK_PER_TRADE_PCT": "0.0025",
    "MAX_POSITION_PCT": "0.05",
    "MAX_OPEN_POSITIONS": "3",
    "DAILY_LOSS_LIMIT_PCT": "0.01",
    "STOP_LIMIT_BUFFER_PCT": "0.001",
    "BREAKOUT_STOP_BUFFER_PCT": "0.001",
    "ENTRY_STOP_PRICE_BUFFER": "0.01",
    "ENTRY_WINDOW_START": "10:00",
    "ENTRY_WINDOW_END": "15:30",
    "FLATTEN_TIME": "15:45",
    "ATR_PERIOD": "14",
}


def make_settings(**overrides: str) -> Settings:
    values = dict(BASE_ENV)
    values.update(overrides)
    return Settings.from_env(values)


def test_slipped_buy_is_adverse_up():
    runner = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="20"))
    assert runner._slipped(100.0, side="buy") == 100.2  # +20 bps


def test_slipped_sell_is_adverse_down():
    runner = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="20"))
    assert runner._slipped(100.0, side="sell") == 99.8  # -20 bps


def test_slipped_zero_bps_is_identity():
    runner = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="0"))
    assert runner._slipped(123.456, side="buy") == 123.456
    assert runner._slipped(123.456, side="sell") == 123.456


def test_costed_replay_never_beats_frictionless():
    """End-to-end directional check on the golden breakout scenario.

    Compares return_pct, not pnl: the slipped entry price feeds position
    sizing, so quantity can differ between runs and pnl is not directly
    comparable per trade. return_pct = (exit - entry) / entry is
    quantity-independent and must be strictly worse with costs.
    """
    scenario = ReplayRunner.load_scenario(GOLDEN_DIR / "breakout_success.json")

    free = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="0")).run(scenario)
    costed = ReplayRunner(make_settings(REPLAY_SLIPPAGE_BPS="20")).run(scenario)

    free_trades = free.backtest_report.trades
    costed_trades = costed.backtest_report.trades
    assert len(free_trades) == len(costed_trades)  # triggers use unslipped prices
    assert len(free_trades) >= 1, "golden scenario must produce at least one trade"
    for f, c in zip(free_trades, costed_trades):
        assert c.entry_price >= f.entry_price
        assert c.exit_price <= f.exit_price
        assert c.return_pct < f.return_pct
```

(`test_replay_golden.py::test_breakout_success_golden_scenario` runs this scenario with
the same env dict and no overrides, so it is guaranteed to produce trades.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_replay_slippage.py -v`
Expected: FAIL with `AttributeError: 'ReplayRunner' object has no attribute '_slipped'`

- [ ] **Step 4: Implement in `src/alpaca_bot/replay/runner.py`**

Add method to `ReplayRunner` (after `load_scenario`):

```python
    def _slipped(self, price: float, *, side: str) -> float:
        """Apply adverse slippage to a simulated fill price.

        Buys fill higher, sells fill lower, by replay_slippage_bps per side.
        Absorbs spread cost and the optimism of fill-at-touch limit exits.
        """
        bps = self.settings.replay_slippage_bps
        if bps <= 0.0:
            return price
        factor = 1.0 + bps / 10_000.0 if side == "buy" else 1.0 - bps / 10_000.0
        return round(price * factor, 4)
```

Four fill sites:

1. `_process_existing_order` — after the `fill_price is None` early-return (line ~201), before `calculate_position_size`:

```python
        # Adverse slippage on entry, capped at the limit (a stop-limit order
        # cannot legally fill above its limit price).
        fill_price = min(self._slipped(fill_price, side="buy"), order.limit_price)
```

2. `_process_stop_hit` (line ~253) — replace

```python
            exit_price = min(position.stop_price, bar.open)
```

with

```python
            exit_price = self._slipped(min(position.stop_price, bar.open), side="sell")
```

3. `_process_profit_target_hit` — after the `bar.high < target_price` early-return (line ~291), insert and use a slipped exit price; replace the event/equity lines:

```python
        exit_price = self._slipped(target_price, side="sell")
        events.append(
            ReplayEvent(
                event_type=IntentType.PROFIT_TARGET_HIT,
                symbol=position.symbol,
                timestamp=bar.timestamp,
                details={"exit_price": round(exit_price, 2)},
            )
        )
        state.equity += (exit_price - position.entry_price) * position.quantity
```

(the trigger condition still compares `bar.high` to the unslipped `target_price`)

4. `_handle_eod_exit` (line ~318) — insert at the top after the position-None guard, then use `exit_price` in the event details and equity update:

```python
        exit_price = self._slipped(bar.close, side="sell")
```

```python
                details={"exit_price": round(exit_price, 2)},
```

```python
        state.equity += (exit_price - position.entry_price) * position.quantity
```

- [ ] **Step 5: Run the full suite** (fixture pinning + slippage must leave all 1,954 green and add the new ones)

Run: `pytest`
Expected: PASS. If a test fails on exact fill arithmetic, its module is missing the `"REPLAY_SLIPPAGE_BPS": "0"` pin from Step 1 — add it there, do not weaken assertions.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/replay/runner.py tests/unit/test_replay_slippage.py tests/unit/
git commit -m "feat: apply adverse slippage at all four replay fill sites

Sweep and nightly inherit costs via the shared runner. Existing test
fixtures pin REPLAY_SLIPPAGE_BPS=0 to keep frictionless golden expectations.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Bootstrap statistics (`replay/stats.py`)

**Files:**
- Create: `src/alpaca_bot/replay/stats.py`
- Test: `tests/unit/test_replay_stats.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
from alpaca_bot.replay.stats import bootstrap_mean_ci, bootstrap_p_positive


def test_ci_none_below_min_samples():
    assert bootstrap_mean_ci([1.0, 2.0, 3.0, 4.0]) is None
    assert bootstrap_p_positive([1.0, -2.0]) is None


def test_ci_all_positive_excludes_zero():
    values = [5.0, 7.0, 6.0, 8.0, 5.5, 7.5, 6.5, 9.0]
    lo, hi = bootstrap_mean_ci(values)
    assert 0 < lo < hi
    assert bootstrap_p_positive(values) == 0.0


def test_ci_symmetric_values_span_zero():
    values = [10.0, -10.0, 8.0, -8.0, 6.0, -6.0, 4.0, -4.0]
    lo, hi = bootstrap_mean_ci(values)
    assert lo < 0 < hi
    p = bootstrap_p_positive(values)
    assert 0.2 < p < 0.8


def test_deterministic_with_seed():
    values = [1.0, -2.0, 3.0, -1.0, 2.0, 0.5]
    assert bootstrap_mean_ci(values) == bootstrap_mean_ci(values)
    assert bootstrap_p_positive(values) == bootstrap_p_positive(values)


def test_constant_values_degenerate_interval():
    values = [2.0] * 10
    lo, hi = bootstrap_mean_ci(values)
    assert lo == hi == 2.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_replay_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alpaca_bot.replay.stats'`

- [ ] **Step 3: Implement `src/alpaca_bot/replay/stats.py`**

```python
"""Bootstrap statistics for per-trade P&L samples.

Pure functions, deterministic via seeded RNG. Used by the strategy audit
to put confidence intervals on backtest edges instead of point estimates.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

MIN_SAMPLES = 5


def _bootstrap_means(
    values: Sequence[float], n_resamples: int, seed: int
) -> list[float]:
    rng = random.Random(seed)
    n = len(values)
    return sorted(
        sum(rng.choices(values, k=n)) / n for _ in range(n_resamples)
    )


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, float] | None:
    """Percentile-bootstrap confidence interval for the mean.

    Returns None when fewer than MIN_SAMPLES values — an interval from a
    handful of trades is more misleading than no interval.
    """
    if len(values) < MIN_SAMPLES:
        return None
    means = _bootstrap_means(values, n_resamples, seed)
    alpha = (1.0 - confidence) / 2.0
    lower_idx = int(alpha * n_resamples)
    upper_idx = min(int((1.0 - alpha) * n_resamples), n_resamples - 1)
    return means[lower_idx], means[upper_idx]


def bootstrap_p_positive(
    values: Sequence[float],
    *,
    n_resamples: int = 2000,
    seed: int = 42,
) -> float | None:
    """One-sided bootstrap p-value for 'mean > 0': fraction of resample
    means that are <= 0. Returns None below MIN_SAMPLES."""
    if len(values) < MIN_SAMPLES:
        return None
    means = _bootstrap_means(values, n_resamples, seed)
    return sum(1 for m in means if m <= 0.0) / n_resamples
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_replay_stats.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/stats.py tests/unit/test_replay_stats.py
git commit -m "feat: bootstrap CI and p-value for per-trade P&L samples

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Audit core (`replay/audit.py`)

**Files:**
- Create: `src/alpaca_bot/replay/audit.py`
- Test: `tests/unit/test_replay_audit.py` (new)

DI seam: `run_audit` takes a `pooled_trades_fn` callable (default = real replay) so tests
inject a fake returning synthetic `ReplayTradeRecord`s — project convention, no mocks.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone

from alpaca_bot.config import Settings
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_replay_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'alpaca_bot.replay.audit'`

- [ ] **Step 3: Implement `src/alpaca_bot/replay/audit.py`**

```python
"""Cost-aware, significance-aware audit of every strategy across scenarios.

Runs each strategy twice (frictionless and with slippage), pools per-trade
P&L across all scenarios, and classifies the edge with bootstrap statistics.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.report import ReplayTradeRecord, report_from_records
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.stats import MIN_SAMPLES, bootstrap_mean_ci, bootstrap_p_positive
from alpaca_bot.strategy import STRATEGY_REGISTRY

AUDIT_STARTING_EQUITY = 100_000.0

PooledTradesFn = Callable[
    [Sequence["ReplayScenario"], Settings, str], list[ReplayTradeRecord]
]


@dataclass(frozen=True)
class StrategyAuditRow:
    strategy: str
    scenarios: int
    trades: int
    win_rate: float | None
    profit_factor: float | None
    total_pnl: float
    mean_trade_pnl: float | None
    annualized_sharpe: float | None
    ci_low: float | None
    ci_high: float | None
    p_positive: float | None
    zero_cost_total_pnl: float
    cost_drag: float  # zero_cost_total_pnl - total_pnl (always >= 0)
    verdict: str  # negative-edge | no-evidence | positive-edge | insufficient-data


def classify_verdict(
    *, trades: int, ci: tuple[float, float] | None, p_positive: float | None
) -> str:
    if trades < MIN_SAMPLES or ci is None or p_positive is None:
        return "insufficient-data"
    lo, hi = ci
    if hi < 0.0:
        return "negative-edge"
    if lo > 0.0 and p_positive < 0.05:
        return "positive-edge"
    return "no-evidence"


def _replay_pooled_trades(
    scenarios: Sequence[ReplayScenario], settings: Settings, strategy_name: str
) -> list[ReplayTradeRecord]:
    evaluator = STRATEGY_REGISTRY[strategy_name]
    runner = ReplayRunner(
        settings, signal_evaluator=evaluator, strategy_name=strategy_name
    )
    trades: list[ReplayTradeRecord] = []
    for scenario in scenarios:
        result = runner.run(scenario)
        trades.extend(result.backtest_report.trades)
    return trades


def run_audit(
    *,
    scenarios: Sequence[ReplayScenario],
    settings: Settings,
    strategies: Sequence[str],
    slippage_bps: float,
    pooled_trades_fn: PooledTradesFn = _replay_pooled_trades,
    on_progress: Callable[[str], None] | None = None,
) -> list[StrategyAuditRow]:
    costed = dataclasses.replace(settings, replay_slippage_bps=slippage_bps)
    frictionless = dataclasses.replace(settings, replay_slippage_bps=0.0)

    rows: list[StrategyAuditRow] = []
    for name in strategies:
        cost_trades = pooled_trades_fn(scenarios, costed, name)
        free_trades = pooled_trades_fn(scenarios, frictionless, name)

        report = report_from_records(
            list(cost_trades), AUDIT_STARTING_EQUITY, name
        )
        pnls = [t.pnl for t in cost_trades]
        ci = bootstrap_mean_ci(pnls)
        p = bootstrap_p_positive(pnls)
        total = sum(pnls)
        zero_total = sum(t.pnl for t in free_trades)

        rows.append(
            StrategyAuditRow(
                strategy=name,
                scenarios=len(scenarios),
                trades=len(cost_trades),
                win_rate=report.win_rate,
                profit_factor=report.profit_factor,
                total_pnl=round(total, 2),
                mean_trade_pnl=(
                    round(total / len(cost_trades), 4) if cost_trades else None
                ),
                annualized_sharpe=report.annualized_sharpe,
                ci_low=round(ci[0], 4) if ci is not None else None,
                ci_high=round(ci[1], 4) if ci is not None else None,
                p_positive=p,
                zero_cost_total_pnl=round(zero_total, 2),
                cost_drag=round(zero_total - total, 2),
                verdict=classify_verdict(
                    trades=len(cost_trades), ci=ci, p_positive=p
                ),
            )
        )
        if on_progress is not None:
            on_progress(
                f"{name}: {len(cost_trades)} trades, verdict={rows[-1].verdict}"
            )
    return rows
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_replay_audit.py -v`
Expected: PASS. Note for `test_run_audit_pools_and_computes_cost_drag`: constant +8 pnls
give a degenerate CI of (8.0, 8.0) and p=0.0 → `positive-edge` (deliberate).

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/audit.py tests/unit/test_replay_audit.py
git commit -m "feat: strategy audit core - pooled cost-aware bootstrap verdicts

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `alpaca-bot-backtest audit` subcommand

**Files:**
- Modify: `src/alpaca_bot/replay/cli.py`
- Test: `tests/unit/test_backtest_cli.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_backtest_cli.py`)

`test_backtest_cli.py` mostly fakes the runner for formatter tests; the audit test instead
exercises the real path end-to-end: copy the golden breakout scenario into a tmp directory
and set the required env vars (because `_cmd_audit` calls `Settings.from_env()` with no
args, which reads `os.environ`). Add `main` to the module's existing
`from alpaca_bot.replay.cli import ...` import line if it is not already imported, plus
`import shutil` at the top.

```python
_GOLDEN_SCENARIO = Path(__file__).resolve().parent.parent / "golden" / "breakout_success.json"

# Same env dict as tests/unit/test_replay_golden.py make_settings — the only
# addition is REPLAY_SLIPPAGE_BPS, pinned so the test is deterministic even if
# the ambient environment sets it.
_AUDIT_ENV = {
    "TRADING_MODE": "paper",
    "ENABLE_LIVE_TRADING": "false",
    "STRATEGY_VERSION": "v1-breakout",
    "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
    "MARKET_DATA_FEED": "sip",
    "SYMBOLS": "AAPL,MSFT,SPY",
    "DAILY_SMA_PERIOD": "20",
    "BREAKOUT_LOOKBACK_BARS": "20",
    "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
    "RELATIVE_VOLUME_THRESHOLD": "1.5",
    "ENTRY_TIMEFRAME_MINUTES": "15",
    "RISK_PER_TRADE_PCT": "0.0025",
    "MAX_POSITION_PCT": "0.05",
    "MAX_OPEN_POSITIONS": "3",
    "DAILY_LOSS_LIMIT_PCT": "0.01",
    "STOP_LIMIT_BUFFER_PCT": "0.001",
    "BREAKOUT_STOP_BUFFER_PCT": "0.001",
    "ENTRY_STOP_PRICE_BUFFER": "0.01",
    "ENTRY_WINDOW_START": "10:00",
    "ENTRY_WINDOW_END": "15:30",
    "FLATTEN_TIME": "15:45",
    "ATR_PERIOD": "14",
    "REPLAY_SLIPPAGE_BPS": "0",
}


def _set_audit_env(monkeypatch) -> None:
    for key, value in _AUDIT_ENV.items():
        monkeypatch.setenv(key, value)


def test_audit_subcommand_writes_markdown_and_json(tmp_path, monkeypatch):
    import shutil

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "a.json")
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "b.json")

    out_md = tmp_path / "audit.md"
    out_json = tmp_path / "audit.json"
    rc = main([
        "audit",
        "--scenario-dir", str(scenario_dir),
        "--strategies", "breakout",
        "--slippage-bps", "5",
        "--output", str(out_md),
        "--json", str(out_json),
    ])
    assert rc == 0

    md = out_md.read_text()
    assert "| strategy |" in md
    assert "breakout" in md

    rows = json.loads(out_json.read_text())
    assert len(rows) == 1
    row = rows[0]
    assert row["strategy"] == "breakout"
    assert row["scenarios"] == 2
    assert row["trades"] >= 2  # golden scenario trades once, copied twice
    assert row["verdict"] in (
        "negative-edge", "no-evidence", "positive-edge", "insufficient-data"
    )
    assert row["cost_drag"] >= 0


def test_audit_subcommand_unknown_strategy_fails(tmp_path, monkeypatch):
    import shutil

    _set_audit_env(monkeypatch)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    shutil.copy(_GOLDEN_SCENARIO, scenario_dir / "a.json")
    rc = main(["audit", "--scenario-dir", str(scenario_dir), "--strategies", "bogus"])
    assert rc == 1


def test_audit_subcommand_empty_dir_fails(tmp_path, monkeypatch):
    _set_audit_env(monkeypatch)
    empty = tmp_path / "none"
    empty.mkdir()
    rc = main(["audit", "--scenario-dir", str(empty)])
    assert rc == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_backtest_cli.py -k audit -v`
Expected: FAIL (argparse: `invalid choice: 'audit'`)

- [ ] **Step 3: Implement in `src/alpaca_bot/replay/cli.py`**

Add imports at top:

```python
import dataclasses

from alpaca_bot.replay.audit import StrategyAuditRow, run_audit
```

Add subparser after the sweep block (line ~66):

```python
    # --- audit subcommand ---
    aud_p = subparsers.add_parser(
        "audit",
        help="Cost-aware significance audit of strategies across a scenario directory",
    )
    aud_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    aud_p.add_argument(
        "--strategies",
        default=None,
        metavar="s1,s2,...",
        help="comma-separated strategy names (default: all registered)",
    )
    aud_p.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        metavar="BPS",
        help="cost level for the costed run (default: REPLAY_SLIPPAGE_BPS)",
    )
    aud_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="audit only the first N scenario files (0 = all)",
    )
    aud_p.add_argument("--output", metavar="FILE", default="-")
    aud_p.add_argument("--json", dest="json_path", metavar="FILE", default=None)
```

Add dispatch after the sweep dispatch (line ~75):

```python
    if args.subcommand == "audit":
        return _cmd_audit(args)
```

Add command + formatter after `_cmd_sweep`:

```python
def _cmd_audit(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",")]
        invalid = [n for n in names if n not in STRATEGY_REGISTRY]
        if invalid:
            print(f"Unknown strategies: {', '.join(invalid)}", file=sys.stderr)
            return 1
    else:
        names = list(STRATEGY_REGISTRY)

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

    rows = run_audit(
        scenarios=scenarios,
        settings=settings,
        strategies=names,
        slippage_bps=bps,
        on_progress=lambda msg: print(f"[audit] {msg}", file=sys.stderr),
    )

    _write_output(_format_audit_markdown(rows, slippage_bps=bps), args.output)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps([dataclasses.asdict(r) for r in rows], indent=2)
        )
    return 0


def _format_audit_markdown(rows: list[StrategyAuditRow], *, slippage_bps: float) -> str:
    def fmt(v: float | None, spec: str = ".2f") -> str:
        return "n/a" if v is None else format(v, spec)

    lines = [
        f"# Strategy audit — {slippage_bps:g} bps/side vs frictionless",
        "",
        "| strategy | scenarios | trades | win rate | profit factor | total P&L "
        "| mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) "
        "| frictionless P&L | cost drag | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ci = (
            f"[{fmt(r.ci_low, '.4f')}, {fmt(r.ci_high, '.4f')}]"
            if r.ci_low is not None
            else "n/a"
        )
        lines.append(
            f"| {r.strategy} | {r.scenarios} | {r.trades} "
            f"| {fmt(r.win_rate, '.1%')} | {fmt(r.profit_factor)} "
            f"| {r.total_pnl:.2f} | {fmt(r.mean_trade_pnl, '.4f')} "
            f"| {fmt(r.annualized_sharpe)} | {ci} | {fmt(r.p_positive, '.4f')} "
            f"| {r.zero_cost_total_pnl:.2f} | {r.cost_drag:.2f} | **{r.verdict}** |"
        )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the full suite**

Run: `pytest`
Expected: PASS (baseline + all new tests)

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/replay/cli.py tests/unit/test_backtest_cli.py
git commit -m "feat: alpaca-bot-backtest audit subcommand (cost + significance)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Run the audit and write the contrarian evaluation report

Operator/analysis task — no unit tests; verification is each command's output.
**Precondition:** the R7 evolve container (`deploy-nightly-run-95e2ee14d9d7`) has exited and
`apply_candidate.sh` ran (watcher b1y1jd3hy). Do not start the heavy audit while it runs.

- [ ] **Step 1: Make production cost-awareness explicit in the env file** (root-owned; alpine bind-mount pattern, never sudo)

```bash
docker run --rm -v /etc/alpaca_bot:/mnt alpine sh -c \
  'grep -q REPLAY_SLIPPAGE_BPS /mnt/alpaca-bot.env || printf "\n# Replay/sweep cost model (per-side adverse slippage)\nREPLAY_SLIPPAGE_BPS=5.0\n" >> /mnt/alpaca-bot.env'
```

- [ ] **Step 2: Deploy HEAD** (rebuilds image so the container has audit code; runs migrations; restarts supervisor — fine after hours)

```bash
./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env
```

Expected: deploy completes, `alpaca-bot-ops-check` passes (deploy.sh runs it).

- [ ] **Step 3: Smoke-run the audit (20 scenarios)**

```bash
cd /workspace/alpaca_bot && set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a && \
docker compose -f deploy/compose.yaml run --rm nightly \
  alpaca-bot-backtest audit --scenario-dir /data/scenarios --limit 20 \
  --output /data/audit-smoke.md --json /data/audit-smoke.json
```

Expected: exit 0, `/var/lib/alpaca-bot/nightly/audit-smoke.md` contains an 11-row table.

- [ ] **Step 4: Full audit run (999 scenarios × 11 strategies × 2 cost levels) in background**

```bash
cd /workspace/alpaca_bot && set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a && \
docker compose -f deploy/compose.yaml run --rm nightly \
  alpaca-bot-backtest audit --scenario-dir /data/scenarios \
  --output /data/audit-full.md --json /data/audit-full.json
```

Run via background task; progress appears on stderr per strategy.

- [ ] **Step 5: Gather the live-side evidence**

```bash
# Honest per-strategy live P&L (S2 same-session matching), full history
docker exec deploy-postgres-1 psql -U alpaca_bot -d alpaca_bot -c "
WITH exits AS (
  SELECT o.strategy_name, o.symbol, o.fill_price AS exit_price, o.filled_quantity AS qty,
         o.updated_at AS exit_at,
         (SELECT e.fill_price FROM orders e
           WHERE e.symbol = o.symbol AND e.intent_type = 'entry' AND e.status = 'filled'
             AND e.updated_at <= o.updated_at
             AND (e.updated_at AT TIME ZONE 'America/New_York')::date
                 = (o.updated_at AT TIME ZONE 'America/New_York')::date
           ORDER BY e.updated_at DESC LIMIT 1) AS entry_price
  FROM orders o
  WHERE o.intent_type IN ('exit','stop') AND o.status = 'filled' AND o.side = 'sell')
SELECT strategy_name, COUNT(*) FILTER (WHERE entry_price IS NOT NULL) AS matched,
       COUNT(*) FILTER (WHERE entry_price IS NULL) AS unmatched,
       ROUND(SUM((exit_price - entry_price) * qty) FILTER (WHERE entry_price IS NOT NULL)::numeric, 2) AS matched_pnl
FROM exits GROUP BY 1 ORDER BY matched_pnl;"

# Option strategies: premium vs buy-to-close + dispatch failure rates
docker exec deploy-postgres-1 psql -U alpaca_bot -d alpaca_bot -c "
SELECT strategy_name,
       COUNT(*) FILTER (WHERE status='failed') AS failed_orders,
       COUNT(*) FILTER (WHERE status='filled' AND side='sell') AS sells,
       COUNT(*) FILTER (WHERE status='filled' AND side='buy') AS buys,
       ROUND((SUM(fill_price*filled_quantity*100) FILTER (WHERE status='filled' AND side='sell')
            - SUM(fill_price*filled_quantity*100) FILTER (WHERE status='filled' AND side='buy'))::numeric, 2)
         AS premium_net_usd
FROM option_orders GROUP BY 1 ORDER BY 1;"

# Tonight's sweep outcome
docker exec deploy-postgres-1 psql -U alpaca_bot -d alpaca_bot -c "
SELECT created_at, payload FROM audit_events
WHERE event_type='nightly_sweep_completed' ORDER BY created_at DESC LIMIT 1;"
cat /var/lib/alpaca-bot/nightly/candidate.env 2>/dev/null
```

(Adjust the first query if its arithmetic disagrees with
`OrderStore.list_trade_pnl_by_strategy` output — that repository method is the source of
truth; the SQL is a convenience cross-check. Option net premium must subtract the
short_option buy-to-close fills recorded in `orders` as well — note this in the report.)

- [ ] **Step 6: Write the report** `docs/strategy-audit/2026-06-11-contrarian-strategy-evaluation.md`

Structure (from the spec — fill every section with the numbers gathered above):

```markdown
# Contrarian Strategy Evaluation — 2026-06-11

## 1. Methodology audit (can we trust our own numbers?)
   - six weaknesses w/ code refs: costs (fixed this session), significance (fixed),
     multiple comparisons (open), live sample size (~9 days), watchlist survivorship
     (backfill/cli.py:43), attribution gaps (overnight carries, option replay absent)
## 2. Equity strategies — verdict table
   - audit-full.md table + live matched P&L column + enabled/disabled flag
   - per strategy: agreement/disagreement live vs replay, final recommendation
## 3. Option strategies (live-only)
   - net premium, failure rates, verdict
## 4. Sweep integrity check
   - tonight's cost-blind sweep winners vs cost-aware audit verdicts;
     does the OOS gate pass anything the audit calls no-evidence?
## 5. Recommendations (not implemented)
   - OOS gate min trade count; gate on CI lower bound; watchlist rotation;
     multi-regime windows; overnight-carry attribution
```

- [ ] **Step 7: Commit the report (and audit artifacts)**

```bash
mkdir -p docs/strategy-audit
cp /var/lib/alpaca-bot/nightly/audit-full.md docs/strategy-audit/2026-06-11-audit-table.md
git add docs/strategy-audit/
git commit -m "docs: contrarian strategy evaluation report

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** Part 1 → Tasks 1-2; Part 2 → Task 3; Part 3 → Tasks 4-5; Part 4 +
  operational constraints → Task 6. Pooling ambiguity resolved: aggregate metrics are
  recomputed from pooled trades via `report_from_records` (max_drawdown from pooled
  cross-scenario ordering is not meaningful and is deliberately not reported by the audit).
- **Known fixture risk:** default 5 bps changes any Settings built without
  `REPLAY_SLIPPAGE_BPS` — Task 2 Step 1 pins all replay/tuning/nightly/backtest/session-eval
  fixtures to 0; full `pytest` in Task 2 Step 5 catches stragglers.
- **Safety:** no broker paths touched; `evaluate_cycle()` untouched; audit CLI is read-only
  over scenario files; Task 6 deploy uses the standard script after market hours.

## Grilling record (Stage 3, 2026-06-11)

All plan-and-refine domain questions answered from the codebase; three defects found and
fixed in this revision:

1. **Task 2 e2e test asserted `c.pnl < f.pnl`** — unsound: the slipped entry price feeds
   `calculate_position_size`, so quantity can differ between the 0-bps and N-bps runs and a
   losing trade can show a *smaller* absolute loss with costs. Rewritten to assert
   `c.return_pct < f.return_pct` (quantity-independent, strictly worse with adverse
   slippage) plus directional `entry_price`/`exit_price` checks, using the golden
   `breakout_success.json` scenario which is guaranteed to produce trades.
2. **Task 5 test referenced nonexistent helpers** (`_write_scenario_file`,
   `_patch_settings_env` — `test_backtest_cli.py` has no such fixtures; it monkeypatches
   `_cli.Settings` for formatter tests). Rewritten to copy the golden scenario into a tmp
   dir and `monkeypatch.setenv` the full env dict, exercising the real `main(["audit", ...])`.
3. **Task 6 used `docker compose run --entrypoint alpaca-bot-backtest`** — the image has
   `CMD ["alpaca-bot-supervisor"]` and **no** ENTRYPOINT; the repo convention (cron, deploy
   scripts) is `docker compose run --rm nightly <command>`. Fixed in Steps 3–4.

Verified clean (no plan change needed): `Settings.__post_init__` re-validates on
`dataclasses.replace` so the audit's 0-bps/N-bps copies are validated; sweep inherits costs
because `tuning/sweep.py` builds `Settings.from_env({**base_env, **overrides})` per combo;
missing/misspelled `REPLAY_SLIPPAGE_BPS` falls back to the conservative 5.0 default (safe
direction); `REPLAY_SLIPPAGE_BPS=0` is an exact rollback; no Postgres, no broker, no
migration, no live/paper divergence anywhere in the change.

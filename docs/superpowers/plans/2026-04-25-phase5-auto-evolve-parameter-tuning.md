# Phase 5 Implementation Plan — Auto-Evolve: Parameter Tuning

**Date**: 2026-04-25  
**Spec**: `docs/superpowers/specs/2026-04-25-phase5-auto-evolve-parameter-tuning.md`  
**Test gate**: `pytest tests/unit/ -q` must be green after every task.

---

## Task 1 — Sharpe ratio in `BacktestReport`

**File**: `src/alpaca_bot/replay/report.py`

Add `_compute_sharpe(trades)` function and replace the `sharpe_ratio=None` stub.

```python
def _compute_sharpe(trades: list[ReplayTradeRecord]) -> float | None:
    n = len(trades)
    if n < 2:
        return None
    returns = [t.return_pct for t in trades]
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = variance ** 0.5
    if std_r == 0.0:
        return None
    return mean_r / std_r
```

In `build_backtest_report()`, replace:
```python
    sharpe_ratio=None,
```
with:
```python
    sharpe_ratio=_compute_sharpe(trades),
```

**Test command**: `pytest tests/unit/test_replay_report.py -q`

---

## Task 2 — New `tuning` module

**File**: `src/alpaca_bot/tuning/__init__.py`

```python
from __future__ import annotations

from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    ParameterGrid,
    TuningCandidate,
    run_sweep,
    score_report,
)

__all__ = ["DEFAULT_GRID", "ParameterGrid", "TuningCandidate", "run_sweep", "score_report"]
```

**File**: `src/alpaca_bot/tuning/sweep.py`

```python
from __future__ import annotations

import itertools
from dataclasses import dataclass

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import ReplayScenario
from alpaca_bot.replay.report import BacktestReport
from alpaca_bot.replay.runner import ReplayRunner


@dataclass(frozen=True)
class TuningCandidate:
    params: dict[str, str]
    report: BacktestReport | None
    score: float | None


ParameterGrid = dict[str, list[str]]

DEFAULT_GRID: ParameterGrid = {
    "BREAKOUT_LOOKBACK_BARS": ["15", "20", "25", "30"],
    "RELATIVE_VOLUME_THRESHOLD": ["1.3", "1.5", "1.8", "2.0"],
    "DAILY_SMA_PERIOD": ["10", "20", "30"],
}


def score_report(report: BacktestReport, *, min_trades: int = 3) -> float | None:
    """Sharpe-first composite score; None if disqualified (< min_trades)."""
    if report.total_trades < min_trades:
        return None
    if report.sharpe_ratio is not None:
        return report.sharpe_ratio
    if report.mean_return_pct is None:
        return None
    drawdown = report.max_drawdown_pct or 0.0
    return report.mean_return_pct / (drawdown + 0.001)


def run_sweep(
    *,
    scenario: ReplayScenario,
    base_env: dict[str, str],
    grid: ParameterGrid | None = None,
    min_trades: int = 3,
) -> list[TuningCandidate]:
    """Run a parameter grid sweep over `scenario`.

    Returns candidates sorted descending by score (scored first, then unscored).
    """
    effective_grid = grid if grid is not None else DEFAULT_GRID
    keys = list(effective_grid.keys())
    value_lists = [effective_grid[k] for k in keys]

    candidates: list[TuningCandidate] = []
    for combo in itertools.product(*value_lists):
        overrides = dict(zip(keys, combo))
        merged_env = {**base_env, **overrides}
        try:
            settings = Settings.from_env(merged_env)
        except ValueError:
            continue  # invalid combination — skip silently

        runner = ReplayRunner(settings)
        result = runner.run(scenario)
        report: BacktestReport | None = result.backtest_report  # type: ignore[assignment]
        s = score_report(report, min_trades=min_trades) if report is not None else None
        candidates.append(TuningCandidate(params=overrides, report=report, score=s))

    return sorted(
        candidates,
        key=lambda c: (c.score is not None, c.score or 0.0),
        reverse=True,
    )
```

**Test command**: `pytest tests/unit/ -q`

---

## Task 3 — Migration: `tuning_results` table

**File**: `migrations/004_add_tuning_results.sql`

```sql
CREATE TABLE IF NOT EXISTS tuning_results (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    scenario_name TEXT NOT NULL,
    trading_mode TEXT NOT NULL,
    params JSONB NOT NULL,
    score DOUBLE PRECISION,
    total_trades INTEGER NOT NULL DEFAULT 0,
    win_rate DOUBLE PRECISION,
    mean_return_pct DOUBLE PRECISION,
    max_drawdown_pct DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    is_best BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_tuning_results_trading_mode_created
    ON tuning_results (trading_mode, created_at DESC);
```

No down migration required (offline tool; data is non-critical).

**Test command**: `pytest tests/unit/ -q`

---

## Task 4 — `TuningResultStore` in `storage/repositories.py`

Add at the bottom of `src/alpaca_bot/storage/repositories.py`:

```python
import uuid as _uuid_module


class TuningResultStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save_run(
        self,
        *,
        scenario_name: str,
        trading_mode: str,
        candidates: list,  # list[TuningCandidate] — avoid circular import
        created_at: datetime,
        run_id: str | None = None,
    ) -> str:
        """Persist all candidates for one sweep run. Returns the run_id used."""
        rid = run_id or str(_uuid_module.uuid4())
        scored = [c for c in candidates if c.score is not None]
        best_params = scored[0].params if scored else None

        for candidate in candidates:
            is_best = bool(best_params and candidate.params == best_params)
            report = candidate.report
            execute(
                self._connection,
                """
                INSERT INTO tuning_results (
                    run_id, created_at, scenario_name, trading_mode,
                    params, score, total_trades, win_rate,
                    mean_return_pct, max_drawdown_pct, sharpe_ratio, is_best
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rid,
                    created_at,
                    scenario_name,
                    trading_mode,
                    json.dumps(candidate.params),
                    candidate.score,
                    report.total_trades if report is not None else 0,
                    report.win_rate if report is not None else None,
                    report.mean_return_pct if report is not None else None,
                    report.max_drawdown_pct if report is not None else None,
                    report.sharpe_ratio if report is not None else None,
                    is_best,
                ),
            )
        return rid

    def load_latest_best(self, *, trading_mode: str) -> dict | None:
        """Return the most recent is_best=TRUE row as a plain dict, or None."""
        row = fetch_one(
            self._connection,
            """
            SELECT params, score, total_trades, win_rate, sharpe_ratio, created_at
            FROM tuning_results
            WHERE trading_mode = %s AND is_best = TRUE
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (trading_mode,),
        )
        if row is None:
            return None
        params = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return {
            "params": params,
            "score": row[1],
            "total_trades": row[2],
            "win_rate": row[3],
            "sharpe_ratio": row[4],
            "created_at": row[5],
        }
```

**Required import additions at top of `repositories.py`**:
- `import json` (may already be there — check)
- `from datetime import date, datetime` (add `datetime` if missing)

**Test command**: `pytest tests/unit/ -q`

---

## Task 5 — CLI: `src/alpaca_bot/tuning/cli.py`

```python
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.tuning.sweep import DEFAULT_GRID, ParameterGrid, TuningCandidate, run_sweep


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-evolve")
    parser.add_argument("--scenario", required=True, metavar="FILE",
                        help="Replay scenario (JSON or YAML)")
    parser.add_argument("--params-grid", metavar="FILE",
                        help="Parameter grid (JSON/YAML); defaults to built-in grid")
    parser.add_argument("--output-env", metavar="FILE",
                        help="Write winning env block to FILE")
    parser.add_argument("--min-trades", type=int, default=3,
                        help="Minimum trades required to score a candidate (default: 3)")
    parser.add_argument("--no-db", action="store_true",
                        help="Skip DB persistence (just print results)")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    settings = Settings.from_env()
    runner = ReplayRunner(settings)
    scenario = runner.load_scenario(args.scenario)

    grid: ParameterGrid = DEFAULT_GRID
    if args.params_grid:
        grid = _load_grid(args.params_grid)

    base_env = dict(os.environ)
    total_combos = 1
    for vals in grid.values():
        total_combos *= len(vals)
    print(f"Running sweep: {total_combos} combinations over scenario '{scenario.name}'...")

    now = datetime.now(timezone.utc)
    candidates = run_sweep(
        scenario=scenario,
        base_env=base_env,
        grid=grid,
        min_trades=args.min_trades,
    )

    scored = [c for c in candidates if c.score is not None]
    unscored = [c for c in candidates if c.score is None]
    print(f"Scored: {len(scored)} / {len(candidates)} candidates "
          f"({len(unscored)} disqualified, min_trades={args.min_trades})")

    best = scored[0] if scored else None

    _print_top_candidates(scored[:10])

    if best is None:
        print("\nNo scored candidates — increase --min-trades or provide a longer scenario.")
        return 1

    env_block = _format_env_block(best, now)
    print(f"\n{env_block}")

    if args.output_env:
        Path(args.output_env).write_text(env_block + "\n")
        print(f"Winning env block written to {args.output_env}")

    if not args.no_db:
        _save_to_db(
            settings=settings,
            candidates=candidates,
            scenario_name=scenario.name,
            now=now,
        )

    return 0


def _load_grid(path: str) -> ParameterGrid:
    p = Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    return {k: [str(v) for v in vals] for k, vals in raw.items()}


def _print_top_candidates(scored: list[TuningCandidate]) -> None:
    if not scored:
        return
    print("\nTop candidates:")
    for i, c in enumerate(scored, 1):
        report = c.report
        trades = report.total_trades if report else 0
        win = f"{report.win_rate:.0%}" if (report and report.win_rate is not None) else "—"
        sharpe = f"{c.score:.4f}" if c.score is not None else "—"
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  {params_str}")


def _format_env_block(best: TuningCandidate, now: datetime) -> str:
    report = best.report
    trades = report.total_trades if report else 0
    win = f"{report.win_rate:.0%}" if (report and report.win_rate is not None) else "—"
    score_str = f"{best.score:.4f}" if best.score is not None else "—"
    lines = [
        f"# Best params from tuning run {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"# Score={score_str}  Trades={trades}  WinRate={win}",
    ]
    lines += [f"{k}={v}" for k, v in best.params.items()]
    return "\n".join(lines)


def _save_to_db(
    *,
    settings: Settings,
    candidates: list[TuningCandidate],
    scenario_name: str,
    now: datetime,
) -> None:
    from alpaca_bot.storage.db import connect_postgres
    from alpaca_bot.storage.repositories import TuningResultStore
    conn = connect_postgres(settings.database_url)
    try:
        store = TuningResultStore(conn)
        run_id = store.save_run(
            scenario_name=scenario_name,
            trading_mode=settings.trading_mode.value,
            candidates=candidates,
            created_at=now,
        )
        print(f"Results saved to DB (run_id={run_id})")
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()
```

**Test command**: `pytest tests/unit/ -q`

---

## Task 6 — Register `alpaca-bot-evolve` in `pyproject.toml`

In `[project.scripts]`, add:
```
alpaca-bot-evolve = "alpaca_bot.tuning.cli:main"
```

Run `pip install -e ".[dev]" --break-system-packages -q` to register the entry point.

**Test command**: `alpaca-bot-evolve --help` → should print usage.

---

## Task 7 — Populate `MetricsSnapshot.last_backtest` from `TuningResultStore`

**File**: `src/alpaca_bot/web/service.py`

In `load_metrics_snapshot()`, after building the `MetricsSnapshot`, load the latest best tuning result:

```python
from alpaca_bot.storage.repositories import TuningResultStore  # add to existing import

# Inside load_metrics_snapshot():
tuning_store = TuningResultStore(connection)
last_tuning = tuning_store.load_latest_best(trading_mode=settings.trading_mode.value)

return MetricsSnapshot(
    ...
    last_backtest=last_tuning,
)
```

The `last_tuning` dict will be rendered in the dashboard template (already handled in Phase 4 — the template shows the `last_backtest` panel). Since it's a dict now (not a `BacktestReport` object), update the template to use dict key access instead of attribute access.

**Template update** (`src/alpaca_bot/web/templates/dashboard.html`): the `last_backtest` panel already uses attribute access (`.win_rate`, `.total_trades` etc.). Since `load_latest_best()` returns a plain dict, change:
- `metrics.last_backtest.total_trades` → `metrics.last_backtest['total_trades']`
- `metrics.last_backtest.win_rate` → `metrics.last_backtest['win_rate']`
- `metrics.last_backtest.mean_return_pct` → `metrics.last_backtest.get('mean_return_pct')` (missing key safety)

Actually: to avoid template dict-vs-attribute divergence, use a `SimpleNamespace` in `load_latest_best()` return to support attribute access, OR use a proper dataclass. The cleanest is to keep it as a dict and update the template to use `metrics.last_backtest.win_rate` via Jinja2's dict attribute fallback (`dict.win_rate` → `dict['win_rate']` in Jinja2 — Jinja2 tries attribute access first, then item access). So no template change needed if `last_backtest` is a dict.

**Test command**: `pytest tests/unit/test_web_service.py -q`

---

## Task 8 — Tests

### 8a — Sharpe ratio tests (add to `tests/unit/test_replay_report.py`)

```python
def test_sharpe_ratio_two_wins() -> None:
    result = _make_result([
        _fill(entry_price=150.0, quantity=10, t=_T0),
        _eod_exit(exit_price=155.0, t=_T1),  # return_pct = 5/150
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 155.0, "quantity": 10, "initial_stop_price": 153.0}),
        _eod_exit(exit_price=160.0, t=_T2),  # return_pct = 5/155
    ])
    report = build_backtest_report(result)
    assert report.sharpe_ratio is not None
    assert report.sharpe_ratio > 0  # both positive returns


def test_sharpe_ratio_none_for_single_trade() -> None:
    result = _make_result([_fill(), _eod_exit(exit_price=155.0)])
    report = build_backtest_report(result)
    assert report.sharpe_ratio is None  # need n >= 2


def test_sharpe_ratio_none_for_identical_returns() -> None:
    """std_dev = 0 when all returns identical → sharpe is None."""
    result = _make_result([
        _fill(entry_price=100.0, quantity=10, t=_T0),
        _eod_exit(exit_price=110.0, t=_T1),  # 10% return
        ReplayEvent(event_type=IntentType.ENTRY_FILLED, symbol="AAPL", timestamp=_T1,
                    details={"entry_price": 100.0, "quantity": 10, "initial_stop_price": 98.0}),
        _eod_exit(exit_price=110.0, t=_T2),  # 10% return — identical
    ])
    report = build_backtest_report(result)
    assert report.sharpe_ratio is None
```

### 8b — Tuning sweep tests: `tests/unit/test_tuning_sweep.py`

```python
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, ReplayScenario
from alpaca_bot.replay.report import BacktestReport
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    TuningCandidate,
    ParameterGrid,
    run_sweep,
    score_report,
)


def _base_env() -> dict[str, str]:
    return {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://x:x@localhost/x",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL",
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
    }


def _make_quiet_scenario() -> ReplayScenario:
    """A scenario with no breakout signals — every combination produces 0 trades."""
    t0 = datetime(2026, 4, 24, 14, 0, tzinfo=timezone.utc)
    bars = [
        Bar(symbol="AAPL", timestamp=t0 + timedelta(minutes=15 * i),
            open=100.0, high=100.5, low=99.5, close=100.0, volume=500)
        for i in range(30)
    ]
    daily = [
        Bar(symbol="AAPL", timestamp=datetime(2026, 3, 26, 20, 0, tzinfo=timezone.utc) + timedelta(days=i),
            open=89.0 + i, high=90.0 + i, low=88.0 + i, close=90.0 + i, volume=1_000_000)
        for i in range(25)
    ]
    return ReplayScenario(name="quiet", symbol="AAPL", starting_equity=100_000.0,
                          daily_bars=daily, intraday_bars=bars)


def _make_golden_scenario() -> ReplayScenario:
    golden = Path(__file__).resolve().parent.parent / "golden" / "breakout_success.json"
    from alpaca_bot.replay.runner import ReplayRunner
    settings = Settings.from_env(_base_env())
    return ReplayRunner(settings).load_scenario(golden)


# ---------------------------------------------------------------------------
# score_report
# ---------------------------------------------------------------------------

def test_score_report_none_below_min_trades() -> None:
    report = BacktestReport(
        trades=(), total_trades=2, winning_trades=2, losing_trades=0,
        win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=None, sharpe_ratio=1.0,
    )
    assert score_report(report, min_trades=3) is None


def test_score_report_uses_sharpe_when_available() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=4, losing_trades=1,
        win_rate=0.8, mean_return_pct=0.03, max_drawdown_pct=0.1, sharpe_ratio=2.5,
    )
    assert score_report(report, min_trades=3) == pytest.approx(2.5)


def test_score_report_calmar_fallback_when_no_sharpe() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=4, losing_trades=1,
        win_rate=0.8, mean_return_pct=0.03, max_drawdown_pct=0.1, sharpe_ratio=None,
    )
    expected = 0.03 / (0.1 + 0.001)
    assert score_report(report, min_trades=3) == pytest.approx(expected)


def test_score_report_calmar_fallback_zero_drawdown() -> None:
    report = BacktestReport(
        trades=(), total_trades=5, winning_trades=5, losing_trades=0,
        win_rate=1.0, mean_return_pct=0.05, max_drawdown_pct=None, sharpe_ratio=None,
    )
    expected = 0.05 / (0.0 + 0.001)
    assert score_report(report, min_trades=3) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# run_sweep
# ---------------------------------------------------------------------------

def test_run_sweep_quiet_scenario_all_unscored() -> None:
    """Quiet scenario → 0 trades → all candidates score=None."""
    scenario = _make_quiet_scenario()
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["15", "20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=small_grid, min_trades=1)
    assert len(candidates) == 2  # 2 × 1 × 1
    assert all(c.score is None for c in candidates)


def test_run_sweep_golden_scenario_produces_scored_candidates() -> None:
    """Golden scenario has 1 trade — with min_trades=1, some candidates are scored."""
    scenario = _make_golden_scenario()
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=small_grid, min_trades=1)
    assert len(candidates) == 1
    # The golden scenario always produces 1 trade — with min_trades=1 it scores
    assert candidates[0].score is not None


def test_run_sweep_sorted_scored_before_unscored() -> None:
    """Scored candidates must appear before unscored ones."""
    scenario = _make_golden_scenario()
    # Mix: one param value gives 0 trades (impossible breakout lookback), others are normal
    # Use min_trades=1 so the golden scenario's 1-trade run gets scored
    small_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20", "25"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=small_grid, min_trades=1)
    # All scored candidates must precede all unscored
    seen_unscored = False
    for c in candidates:
        if c.score is None:
            seen_unscored = True
        elif seen_unscored:
            pytest.fail("A scored candidate appeared after an unscored one")


def test_run_sweep_skips_invalid_param_combinations() -> None:
    """Invalid Settings values (e.g., non-numeric) should be skipped, not crash."""
    scenario = _make_quiet_scenario()
    grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["20", "NOT_AN_INT"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.5"],
        "DAILY_SMA_PERIOD": ["20"],
    }
    # Must not raise; just return whatever valid combinations produced
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=grid)
    assert isinstance(candidates, list)
    # Only the valid combo (BARS=20) produces a candidate
    assert len(candidates) == 1


def test_run_sweep_custom_grid_overrides_default() -> None:
    """Passing a custom grid respects the provided values, not DEFAULT_GRID."""
    scenario = _make_quiet_scenario()
    custom_grid: ParameterGrid = {
        "BREAKOUT_LOOKBACK_BARS": ["22"],
        "RELATIVE_VOLUME_THRESHOLD": ["1.6"],
        "DAILY_SMA_PERIOD": ["18"],
    }
    candidates = run_sweep(scenario=scenario, base_env=_base_env(), grid=custom_grid)
    assert len(candidates) == 1
    assert candidates[0].params["BREAKOUT_LOOKBACK_BARS"] == "22"
    assert candidates[0].params["RELATIVE_VOLUME_THRESHOLD"] == "1.6"
    assert candidates[0].params["DAILY_SMA_PERIOD"] == "18"
```

**Test command**: `pytest tests/unit/test_tuning_sweep.py tests/unit/test_replay_report.py -q`

---

## Task 9 — Full test gate

```bash
pytest tests/unit/ -q
```

All tests must pass (expect ~315+ tests after Phase 5).

---

## Critical constraints (from grilling)

- `RELATIVE_VOLUME_THRESHOLD` must be > 1.0 per `Settings.validate()` — grid values "1.3", "1.5", "1.8", "2.0" all satisfy this.
- `DAILY_SMA_PERIOD` minimum is 2 — values 10, 20, 30 all satisfy this.
- `BREAKOUT_LOOKBACK_BARS` minimum is 2 — values 15, 20, 25, 30 all satisfy this.
- Score function uses Sharpe when available; n=1 → sharpe=None → falls back to Calmar approximation.
- `TuningResultStore.save_run()` uses `%s::jsonb` cast for JSONB params column — required for psycopg2.
- The `load_latest_best()` return dict is used as `MetricsSnapshot.last_backtest`; Jinja2 supports both dict and attribute access so the template requires no changes.
- The CLI must not call `bootstrap_runtime()` — just `connect_postgres()` directly for DB persistence.
- `--no-db` flag skips all Postgres contact, enabling fully offline use (no DATABASE_URL required if `--no-db`).

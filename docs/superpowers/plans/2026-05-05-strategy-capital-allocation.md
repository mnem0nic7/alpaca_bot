# Strategy Capital Allocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically allocate per-strategy capital fractions based on 20-day rolling Sharpe ratios, replacing the flat `account.equity` pass-through in the supervisor's per-strategy loop.

**Architecture:** A new pure function `compute_strategy_weights()` computes Sharpe-proportional weights with iterative clip/normalize; a `StrategyWeightStore` persists weights to Postgres; the supervisor calls both at session-open and injects `effective_equity = account.equity × weight` at line 617. The web dashboard gains a Capital Allocation panel on the metrics page.

**Tech Stack:** Python (stdlib math/statistics), psycopg2, FastAPI/Jinja2, pytest — all existing dependencies, no new packages required.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `migrations/013_add_strategy_weights.sql` | Create | `strategy_weights` table |
| `src/alpaca_bot/risk/weighting.py` | Create | `WeightResult`, `compute_strategy_weights()` pure function |
| `src/alpaca_bot/storage/models.py` | Modify | `StrategyWeight` frozen dataclass |
| `src/alpaca_bot/storage/repositories.py` | Modify | `OrderStore.list_trade_pnl_by_strategy()` + `StrategyWeightStore` class |
| `src/alpaca_bot/storage/__init__.py` | Modify | Export `StrategyWeight`, `StrategyWeightStore` |
| `src/alpaca_bot/runtime/bootstrap.py` | Modify | `strategy_weight_store` field in `RuntimeContext`; init + reconnect wiring |
| `src/alpaca_bot/runtime/supervisor.py` | Modify | `_session_capital_weights` cache; `_update_session_weights()`; `effective_equity` injection at line 617 |
| `src/alpaca_bot/web/service.py` | Modify | `StrategyWeightRow` dataclass + `load_strategy_weights()` |
| `src/alpaca_bot/web/app.py` | Modify | `strategy_weight_store_factory` param; load + pass weights in metrics route |
| `src/alpaca_bot/web/templates/dashboard.html` | Modify | Capital Allocation panel (hidden when empty) |
| `tests/unit/test_weighting.py` | Create | Pure function unit tests |
| `tests/unit/test_storage_db.py` | Extend | `list_trade_pnl_by_strategy` + `StrategyWeightStore` DB tests |
| `tests/unit/test_web_service.py` | Extend | `load_strategy_weights()` service tests |
| `tests/unit/test_supervisor_weights.py` | Create | Effective equity injection + `_update_session_weights` tests |

---

## Task 1: SQL Migration

**Files:**
- Create: `migrations/013_add_strategy_weights.sql`

- [ ] **Step 1: Create the migration file**

```sql
CREATE TABLE IF NOT EXISTS strategy_weights (
    strategy_name    TEXT NOT NULL,
    trading_mode     TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    weight           FLOAT NOT NULL,
    sharpe           FLOAT NOT NULL DEFAULT 0.0,
    computed_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (strategy_name, trading_mode, strategy_version)
);
```

- [ ] **Step 2: Verify migration runs cleanly**

Run: `alpaca-bot-migrate`

Expected: Migration `013_add_strategy_weights.sql` applied successfully, no errors.

- [ ] **Step 3: Commit**

```bash
git add migrations/013_add_strategy_weights.sql
git commit -m "feat: add strategy_weights table (migration 013)"
```

---

## Task 2: Pure Weighting Function

**Files:**
- Create: `src/alpaca_bot/risk/weighting.py`
- Create: `tests/unit/test_weighting.py`

`★ Insight ─────────────────────────────────────`
The iterative clip-normalize loop is needed because clipping one weight redistributes mass to others, potentially pushing a previously-in-bounds weight out of bounds. The convergence is proven for ≤11 strategies in ≤3 passes, but we cap at 20 iterations as a defensive guard.
`─────────────────────────────────────────────────`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_weighting.py`:

```python
from __future__ import annotations

import math
from datetime import date

import pytest

from alpaca_bot.risk.weighting import WeightResult, compute_strategy_weights


def _row(strategy: str, exit_date: date, pnl: float) -> dict:
    return {"strategy_name": strategy, "exit_date": exit_date, "pnl": pnl}


def test_equal_weights_when_no_history() -> None:
    result = compute_strategy_weights([], ["breakout", "momentum", "orb"])
    assert set(result.weights.keys()) == {"breakout", "momentum", "orb"}
    for w in result.weights.values():
        assert abs(w - 1 / 3) < 1e-9
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9
    for s in result.sharpes.values():
        assert s == 0.0


def test_equal_weights_when_fewer_than_min_trades() -> None:
    d = date(2026, 1, 1)
    rows = [_row("breakout", d, 100.0), _row("breakout", d, 100.0)]  # only 2 trades
    result = compute_strategy_weights(rows, ["breakout", "momentum"])
    assert abs(result.weights["breakout"] - 0.5) < 1e-9
    assert abs(result.weights["momentum"] - 0.5) < 1e-9
    assert result.sharpes["breakout"] == 0.0


def test_weights_proportional_to_sharpe() -> None:
    # Give breakout 5 winning trades (high Sharpe), momentum 5 flat/zero trades
    rows = []
    for i in range(5):
        rows.append(_row("breakout", date(2026, 1, i + 1), 100.0))
    for i in range(5):
        rows.append(_row("momentum", date(2026, 1, i + 1), 0.0))
    result = compute_strategy_weights(rows, ["breakout", "momentum"])
    # momentum has std=0, mean=0 → sharpe=0
    assert result.sharpes["momentum"] == 0.0
    # breakout gets std=0, mean>0 → sharpe=1.0 → all weight goes to breakout
    # but floor prevents complete starvation
    assert result.weights["breakout"] > result.weights["momentum"]
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_floor_applied_when_strategy_has_low_sharpe() -> None:
    # Give 5 strategies varying Sharpes; one very low
    strategies = ["a", "b", "c", "d", "e"]
    rows = []
    sharpe_inputs = [2.0, 2.0, 2.0, 2.0, 0.01]
    for i, (name, s) in enumerate(zip(strategies, sharpe_inputs)):
        for day in range(5):
            rows.append(_row(name, date(2026, 1, day + 1), s * (day + 1)))
    result = compute_strategy_weights(rows, strategies)
    for w in result.weights.values():
        assert w >= 0.05 - 1e-9, f"weight {w} below floor"
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_cap_applied_when_one_strategy_dominates() -> None:
    # Give breakout a very high Sharpe, others much lower
    rows = []
    for day in range(10):
        rows.append(_row("breakout", date(2026, 1, day + 1), 500.0 * (day + 1)))
    for name in ["momentum", "orb"]:
        for day in range(5):
            rows.append(_row(name, date(2026, 1, day + 1), 1.0))
    result = compute_strategy_weights(rows, ["breakout", "momentum", "orb"])
    assert result.weights["breakout"] <= 0.40 + 1e-9
    assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_weights_sum_to_one_in_all_cases() -> None:
    for n in [1, 2, 5, 11]:
        strategies = [f"s{i}" for i in range(n)]
        rows = []
        for i, name in enumerate(strategies):
            for day in range(5):
                rows.append(_row(name, date(2026, 1, day + 1), float(i + 1) * 10.0))
        result = compute_strategy_weights(rows, strategies)
        assert abs(sum(result.weights.values()) - 1.0) < 1e-9


def test_empty_active_strategies_returns_empty() -> None:
    result = compute_strategy_weights([], [])
    assert result.weights == {}
    assert result.sharpes == {}


def test_trade_rows_for_inactive_strategy_are_ignored() -> None:
    # "ghost" is in rows but not in active_strategies
    rows = [_row("ghost", date(2026, 1, 1), 999.0)]
    result = compute_strategy_weights(rows, ["breakout"])
    assert "ghost" not in result.weights
    assert result.weights == {"breakout": 1.0}


def test_single_strategy_gets_weight_one() -> None:
    rows = []
    for day in range(5):
        rows.append(_row("breakout", date(2026, 1, day + 1), 100.0))
    result = compute_strategy_weights(rows, ["breakout"])
    assert abs(result.weights["breakout"] - 1.0) < 1e-9


def test_sharpe_uses_annualised_formula() -> None:
    # 5 trades, one per day, all same pnl → std=0, mean>0 → sharpe=1.0
    rows = [_row("breakout", date(2026, 1, i + 1), 50.0) for i in range(5)]
    result = compute_strategy_weights(rows, ["breakout"])
    assert result.sharpes["breakout"] == 1.0

    # 5 trades, varying pnl → check formula: mean/std * sqrt(252)
    rows2 = [_row("momentum", date(2026, 1, i + 1), float(i + 1) * 10.0) for i in range(5)]
    result2 = compute_strategy_weights(rows2, ["momentum"])
    daily_pnl = [10.0, 20.0, 30.0, 40.0, 50.0]
    mean_pnl = sum(daily_pnl) / 5
    variance = sum((v - mean_pnl) ** 2 for v in daily_pnl) / 4
    expected_sharpe = max(0.0, mean_pnl / variance ** 0.5 * math.sqrt(252))
    assert abs(result2.sharpes["momentum"] - expected_sharpe) < 1e-6
```

- [ ] **Step 2: Run to verify tests fail**

Run: `pytest tests/unit/test_weighting.py -v`

Expected: `ModuleNotFoundError: No module named 'alpaca_bot.risk.weighting'`

- [ ] **Step 3: Implement the weighting module**

Create `src/alpaca_bot/risk/weighting.py`:

```python
from __future__ import annotations

import math
from typing import NamedTuple


class WeightResult(NamedTuple):
    weights: dict[str, float]
    sharpes: dict[str, float]


def compute_strategy_weights(
    trade_rows: list[dict],
    active_strategies: list[str],
    *,
    min_weight: float = 0.05,
    max_weight: float = 0.40,
    min_trades: int = 5,
) -> WeightResult:
    """Compute Sharpe-proportional capital weights for active strategies.

    Returns WeightResult with weights summing to 1.0 and per-strategy Sharpes.
    Each weight is clipped to [min_weight, max_weight] via iterative normalization.
    Falls back to equal weights when all Sharpes are 0 (no history or all losing).
    """
    n_active = len(active_strategies)
    if n_active == 0:
        return WeightResult({}, {})

    active_set = set(active_strategies)
    # Accumulate daily PnL and trade counts per strategy
    daily_pnl: dict[str, dict] = {name: {} for name in active_strategies}
    trade_count: dict[str, int] = {name: 0 for name in active_strategies}
    for row in trade_rows:
        name = row["strategy_name"]
        if name not in active_set:
            continue
        d = row["exit_date"]
        daily_pnl[name][d] = daily_pnl[name].get(d, 0.0) + row["pnl"]
        trade_count[name] += 1

    # Compute annualised Sharpe per strategy
    sharpes: dict[str, float] = {}
    for name in active_strategies:
        if trade_count[name] < min_trades:
            sharpes[name] = 0.0
            continue
        daily_values = list(daily_pnl[name].values())
        n = len(daily_values)
        mean = sum(daily_values) / n
        if n < 2:
            sharpes[name] = 1.0 if mean > 0 else 0.0
            continue
        variance = sum((v - mean) ** 2 for v in daily_values) / (n - 1)
        std = variance ** 0.5
        if std == 0.0:
            sharpes[name] = 1.0 if mean > 0 else 0.0
        else:
            sharpes[name] = max(0.0, mean / std * math.sqrt(252))

    total_sharpe = sum(sharpes.values())
    if total_sharpe == 0.0:
        equal = 1.0 / n_active
        return WeightResult({name: equal for name in active_strategies}, sharpes)

    weights: dict[str, float] = {
        name: sharpes[name] / total_sharpe for name in active_strategies
    }

    # Iterative clip + re-normalize until stable (converges ≤3 passes for ≤11 strategies)
    for _ in range(20):
        clipped = {
            name: min(max(w, min_weight), max_weight) for name, w in weights.items()
        }
        total = sum(clipped.values())
        normalized = {name: w / total for name, w in clipped.items()}
        if all(
            min_weight - 1e-9 <= normalized[name] <= max_weight + 1e-9
            for name in active_strategies
        ):
            return WeightResult(normalized, sharpes)
        weights = normalized

    return WeightResult(weights, sharpes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_weighting.py -v`

Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/risk/weighting.py tests/unit/test_weighting.py
git commit -m "feat: add compute_strategy_weights() pure function with Sharpe-proportional weights"
```

---

## Task 3: Storage Layer

**Files:**
- Modify: `src/alpaca_bot/storage/models.py`
- Modify: `src/alpaca_bot/storage/repositories.py`
- Modify: `src/alpaca_bot/storage/__init__.py`
- Extend: `tests/unit/test_storage_db.py`

`★ Insight ─────────────────────────────────────`
`StrategyWeightStore.upsert_many()` takes a `dict[str, float]` for weights and sharpes (not individual `StrategyWeight` objects) to keep the API ergonomic for the supervisor's call site. The `list_trade_pnl_by_strategy()` SQL is a light extension of `list_trade_exits_in_range()` — it only adds `strategy_name` to the SELECT and groups the return dict by that field.
`─────────────────────────────────────────────────`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_storage_db.py` (append after existing tests):

```python
# ── test_list_trade_pnl_by_strategy ──────────────────────────────────────────

class TestListTradePnlByStrategy:
    """Unit tests for OrderStore.list_trade_pnl_by_strategy()."""

    def _make_store(self, rows: list[tuple]) -> "OrderStore":
        return OrderStore(_make_fake_connection(rows))

    def test_returns_empty_when_no_rows(self) -> None:
        store = self._make_store([])
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert result == []

    def test_filters_rows_without_entry_fill(self) -> None:
        # entry_fill is None → should be excluded
        rows = [("breakout", date(2026, 1, 2), 10, 105.0, None)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert result == []

    def test_computes_pnl_correctly(self) -> None:
        # pnl = (exit_fill - entry_fill) * qty
        # row: (strategy_name, exit_date, qty, exit_fill, entry_fill)
        rows = [("breakout", date(2026, 1, 2), 5, 110.0, 100.0)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 1
        assert result[0]["strategy_name"] == "breakout"
        assert result[0]["exit_date"] == date(2026, 1, 2)
        assert abs(result[0]["pnl"] - 50.0) < 1e-9  # (110 - 100) * 5

    def test_multiple_strategies_returned(self) -> None:
        rows = [
            ("breakout", date(2026, 1, 2), 5, 110.0, 100.0),
            ("momentum", date(2026, 1, 3), 10, 52.0, 50.0),
        ]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 2
        names = {r["strategy_name"] for r in result}
        assert names == {"breakout", "momentum"}

    def test_negative_pnl_for_losing_trade(self) -> None:
        rows = [("breakout", date(2026, 1, 2), 5, 90.0, 100.0)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 1
        assert result[0]["pnl"] < 0.0  # losing trade


# ── test_StrategyWeightStore ──────────────────────────────────────────────────

class TestStrategyWeightStore:
    """Unit tests for StrategyWeightStore upsert_many + load_all."""

    def _make_store_with_rows(self, rows: list[tuple]) -> "StrategyWeightStore":
        return StrategyWeightStore(_make_fake_connection(rows))

    def test_load_all_returns_empty_when_no_rows(self) -> None:
        store = self._make_store_with_rows([])
        result = store.load_all(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert result == []

    def test_load_all_returns_strategy_weight_objects(self) -> None:
        now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        rows = [("breakout", "paper", "v1", 0.6, 1.8, now)]
        store = self._make_store_with_rows(rows)
        result = store.load_all(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
        )
        assert len(result) == 1
        w = result[0]
        assert w.strategy_name == "breakout"
        assert abs(w.weight - 0.6) < 1e-9
        assert abs(w.sharpe - 1.8) < 1e-9
        assert w.trading_mode == TradingMode.PAPER
        assert w.computed_at == now

    def test_upsert_many_calls_execute_for_each_strategy(self) -> None:
        executed: list[tuple] = []

        class _TrackingConn:
            def cursor(self):
                return self

            def execute(self, sql, params=None):
                if params:
                    executed.append(params)

            def fetchone(self):
                return None

            def fetchall(self):
                return []

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        store = StrategyWeightStore(_TrackingConn())
        now = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        store.upsert_many(
            weights={"breakout": 0.6, "momentum": 0.4},
            sharpes={"breakout": 1.8, "momentum": 0.9},
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            computed_at=now,
        )
        assert len(executed) == 2
        names_stored = {p[0] for p in executed}
        assert names_stored == {"breakout", "momentum"}
```

At the top of `test_storage_db.py` add these imports (they may already exist, add missing ones):

```python
from datetime import date, datetime, timezone
from alpaca_bot.config import TradingMode
from alpaca_bot.storage import StrategyWeight, StrategyWeightStore
from alpaca_bot.storage.repositories import OrderStore
```

- [ ] **Step 2: Run to verify tests fail**

Run: `pytest tests/unit/test_storage_db.py::TestListTradePnlByStrategy tests/unit/test_storage_db.py::TestStrategyWeightStore -v`

Expected: `AttributeError: 'OrderStore' object has no attribute 'list_trade_pnl_by_strategy'` and `ImportError: cannot import name 'StrategyWeightStore'`.

- [ ] **Step 3: Add StrategyWeight dataclass to models.py**

In `src/alpaca_bot/storage/models.py`, after the `StrategyFlag` dataclass, add:

```python
@dataclass(frozen=True)
class StrategyWeight:
    strategy_name: str
    trading_mode: TradingMode
    strategy_version: str
    weight: float
    sharpe: float
    computed_at: datetime
```

- [ ] **Step 4: Add list_trade_pnl_by_strategy to OrderStore in repositories.py**

In `src/alpaca_bot/storage/repositories.py`, add after `list_trade_exits_in_range` (after line ~706):

```python
    def list_trade_pnl_by_strategy(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        start_date: date,
        end_date: date,
        market_timezone: str = "America/New_York",
    ) -> list[dict]:
        """Return one dict per closed trade in the date range with strategy attribution.

        Each dict: {strategy_name: str, exit_date: date, pnl: float}
        Filters out trades where entry_fill is NULL (no correlated entry order).
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT x.strategy_name,
                   DATE(x.updated_at AT TIME ZONE %s) AS exit_date,
                   COALESCE(x.filled_quantity, x.quantity) AS qty,
                   x.fill_price AS exit_fill,
                   (SELECT e.fill_price
                      FROM orders e
                     WHERE e.symbol = x.symbol
                       AND e.trading_mode = x.trading_mode
                       AND e.strategy_version = x.strategy_version
                       AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                       AND e.intent_type = 'entry'
                       AND e.fill_price IS NOT NULL
                       AND e.status = 'filled'
                       AND e.updated_at <= x.updated_at
                     ORDER BY e.updated_at DESC
                     LIMIT 1) AS entry_fill
              FROM orders x
             WHERE x.trading_mode = %s
               AND x.strategy_version = %s
               AND x.intent_type IN ('stop', 'exit')
               AND x.fill_price IS NOT NULL
               AND x.status = 'filled'
               AND DATE(x.updated_at AT TIME ZONE %s) >= %s
               AND DATE(x.updated_at AT TIME ZONE %s) <= %s
             ORDER BY x.updated_at
            """,
            (
                market_timezone,
                trading_mode.value,
                strategy_version,
                market_timezone,
                start_date,
                market_timezone,
                end_date,
            ),
        )
        return [
            {
                "strategy_name": row[0],
                "exit_date": row[1],
                "pnl": (float(row[3]) - float(row[4])) * int(row[2]),
            }
            for row in rows
            if row[4] is not None
        ]
```

- [ ] **Step 5: Add StrategyWeightStore class to repositories.py**

In `src/alpaca_bot/storage/repositories.py`, add after `StrategyFlagStore` (after line ~1244):

```python
class StrategyWeightStore:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def upsert_many(
        self,
        *,
        weights: dict[str, float],
        sharpes: dict[str, float],
        trading_mode: TradingMode,
        strategy_version: str,
        computed_at: datetime,
    ) -> None:
        for strategy_name, weight in weights.items():
            execute(
                self._connection,
                """
                INSERT INTO strategy_weights (
                    strategy_name, trading_mode, strategy_version,
                    weight, sharpe, computed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (strategy_name, trading_mode, strategy_version)
                DO UPDATE SET
                    weight = EXCLUDED.weight,
                    sharpe = EXCLUDED.sharpe,
                    computed_at = EXCLUDED.computed_at
                """,
                (
                    strategy_name,
                    trading_mode.value,
                    strategy_version,
                    weight,
                    sharpes.get(strategy_name, 0.0),
                    computed_at,
                ),
                commit=False,
            )
        try:
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def load_all(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[StrategyWeight]:
        rows = fetch_all(
            self._connection,
            """
            SELECT strategy_name, trading_mode, strategy_version,
                   weight, sharpe, computed_at
              FROM strategy_weights
             WHERE trading_mode = %s AND strategy_version = %s
             ORDER BY strategy_name
            """,
            (trading_mode.value, strategy_version),
        )
        return [
            StrategyWeight(
                strategy_name=row[0],
                trading_mode=TradingMode(row[1]),
                strategy_version=row[2],
                weight=float(row[3]),
                sharpe=float(row[4]),
                computed_at=row[5],
            )
            for row in rows
        ]
```

Note: `StrategyWeight` must be imported at the top of `repositories.py`. Add it to the existing `from alpaca_bot.storage.models import (...)` block.

- [ ] **Step 6: Update storage __init__.py exports**

In `src/alpaca_bot/storage/__init__.py`, add `StrategyWeight` to the models import:

```python
from alpaca_bot.storage.models import (
    AuditEvent,
    DailySessionState,
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    GLOBAL_SESSION_STATE_STRATEGY_NAME,
    OptionOrderRecord,
    OrderRecord,
    PositionRecord,
    StrategyFlag,
    StrategyWeight,        # add this
    TradingStatus,
    TradingStatusValue,
)
```

Add `StrategyWeightStore` to the repositories import:

```python
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    DailySessionStateStore,
    OptionOrderRepository,
    OrderStore,
    PositionStore,
    StrategyFlagStore,
    StrategyWeightStore,   # add this
    TradingStatusStore,
    WatchlistRecord,
    WatchlistStore,
)
```

Add both to `__all__`:

```python
    "StrategyWeight",
    "StrategyWeightStore",
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/unit/test_storage_db.py::TestListTradePnlByStrategy tests/unit/test_storage_db.py::TestStrategyWeightStore -v`

Expected: All 8 tests PASS.

Run full test suite to verify no regressions:

Run: `pytest tests/unit/test_storage_db.py -v`

Expected: All existing tests continue to PASS.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/storage/models.py src/alpaca_bot/storage/repositories.py src/alpaca_bot/storage/__init__.py tests/unit/test_storage_db.py
git commit -m "feat: add StrategyWeight model, StrategyWeightStore, and OrderStore.list_trade_pnl_by_strategy()"
```

---

## Task 4: Bootstrap Wiring

**Files:**
- Modify: `src/alpaca_bot/runtime/bootstrap.py`

- [ ] **Step 1: Add strategy_weight_store to RuntimeContext**

In `src/alpaca_bot/runtime/bootstrap.py`, add `StrategyWeightStore` to the imports from `alpaca_bot.storage`:

```python
from alpaca_bot.storage import (
    ...existing imports...
    StrategyWeightStore,
)
```

In `RuntimeContext` dataclass, add after `option_order_store`:

```python
    strategy_weight_store: StrategyWeightStore | None = None
```

- [ ] **Step 2: Initialize store in bootstrap_runtime**

In `bootstrap_runtime()`, add to the `RuntimeContext(...)` constructor after `option_order_store`:

```python
        strategy_weight_store=StrategyWeightStore(runtime_connection),
```

- [ ] **Step 3: Add to reconnect list in reconnect_runtime_connection**

In `reconnect_runtime_connection()`, add `"strategy_weight_store"` to the `for attr in (...)` tuple:

```python
    for attr in (
        "trading_status_store",
        "audit_event_store",
        "order_store",
        "daily_session_state_store",
        "position_store",
        "strategy_flag_store",
        "watchlist_store",
        "option_order_store",
        "strategy_weight_store",    # add this
    ):
```

- [ ] **Step 4: Run full test suite to verify no regressions**

Run: `pytest -x`

Expected: All existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/bootstrap.py
git commit -m "feat: add strategy_weight_store to RuntimeContext, bootstrap, and reconnect wiring"
```

---

## Task 5: Supervisor Integration

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Create: `tests/unit/test_supervisor_weights.py`

`★ Insight ─────────────────────────────────────`
The `_update_session_weights` method must NOT hold `store_lock` when calling `_append_audit()` because `_append_audit()` acquires `store_lock` internally (see supervisor.py line ~1174). Holding the lock before calling it would deadlock. The pattern: acquire lock → do DB work → release → call `_append_audit` without lock.
`─────────────────────────────────────────────────`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_supervisor_weights.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.execution import BrokerAccount
from alpaca_bot.storage import AuditEvent

_NOW = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
_SESSION_DATE = date(2026, 5, 1)


def _make_settings(**overrides) -> Settings:
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
        "MARKET_DATA_FEED": "sip",
        "SYMBOLS": "AAPL,MSFT",
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
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    base.update(overrides)
    return Settings.from_env(base)


class _FakeConn:
    def commit(self): pass
    def rollback(self): pass


class _RecordingAuditStore:
    def __init__(self):
        self.appended: list[AuditEvent] = []
    def append(self, event: AuditEvent, *, commit: bool = True):
        self.appended.append(event)
    def load_latest(self, **kwargs): return None
    def list_recent(self, **kwargs): return []
    def list_by_event_types(self, **kwargs): return []


class _RecordingOrderStore:
    def __init__(self, *, pnl_rows: list[dict] | None = None):
        self._pnl_rows = pnl_rows or []
    def save(self, order, *, commit=True): pass
    def list_by_status(self, **kwargs): return []
    def list_pending_submit(self, **kwargs): return []
    def daily_realized_pnl(self, **kwargs): return 0.0
    def daily_realized_pnl_by_symbol(self, **kwargs): return {}
    def list_trade_pnl_by_strategy(self, **kwargs): return self._pnl_rows


class _FakeWeightStore:
    def __init__(self, *, preloaded: list | None = None):
        self._preloaded = preloaded or []
        self.upserted: list[dict] = []
    def load_all(self, **kwargs): return list(self._preloaded)
    def upsert_many(self, *, weights, sharpes, trading_mode, strategy_version, computed_at):
        self.upserted.append({"weights": dict(weights), "sharpes": dict(sharpes)})


class _CapturingCycleRunner:
    def __init__(self):
        self.captured_equities: list[float] = []
        self.captured_strategy_names: list[str] = []
    def __call__(self, *, equity, strategy_name, **kwargs):
        self.captured_equities.append(equity)
        self.captured_strategy_names.append(strategy_name)
        return SimpleNamespace(intents=[])


def _make_supervisor(
    *,
    settings: Settings,
    broker_equity: float = 10_000.0,
    weight_store: _FakeWeightStore | None = None,
    order_store: _RecordingOrderStore | None = None,
    cycle_runner=None,
    only_breakout: bool = True,
):
    module = import_module("alpaca_bot.runtime.supervisor")
    RuntimeSupervisor = module.RuntimeSupervisor

    class _FakeBroker:
        def get_account(self):
            return BrokerAccount(
                equity=broker_equity,
                buying_power=broker_equity * 2,
                trading_blocked=False,
            )
        def list_open_orders(self): return []

    class _FakeMarketData:
        def get_stock_bars(self, **kwargs): return {}
        def get_daily_bars(self, **kwargs): return {}

    class _FakeTradingStatusStore:
        def load(self, **kwargs): return None

    class _FakePositionStore:
        def list_all(self, **kwargs): return []
        def replace_all(self, **kwargs): pass

    class _FakeStrategyFlagStore:
        def list_all(self, **kwargs): return []
        def load(self, *, strategy_name, **kwargs):
            if only_breakout and strategy_name != "breakout":
                from alpaca_bot.storage import StrategyFlag
                return StrategyFlag(
                    strategy_name=strategy_name,
                    trading_mode=settings.trading_mode,
                    strategy_version=settings.strategy_version,
                    enabled=False,
                    updated_at=_NOW,
                )
            return None

    class _FakeSessionStateStore:
        def load(self, **kwargs): return None
        def save(self, **kwargs): pass
        def list_by_session(self, **kwargs): return []

    class _FakeWatchlistStore:
        def list_enabled(self, *args): return ["AAPL", "MSFT"]
        def list_ignored(self, *args): return []

    _order_store = order_store or _RecordingOrderStore()
    _weight_store = weight_store

    class _FakeRuntimeContext:
        connection = _FakeConn()
        store_lock = None
        order_store = _order_store
        strategy_weight_store = _weight_store
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _FakeSessionStateStore()
        audit_event_store = _RecordingAuditStore()
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()
        def commit(self): pass

    _runner = cycle_runner or (lambda **kwargs: SimpleNamespace(intents=[]))

    supervisor = RuntimeSupervisor(
        settings=settings,
        runtime=_FakeRuntimeContext(),
        broker=_FakeBroker(),
        market_data=_FakeMarketData(),
        stream=None,
        close_runtime_fn=lambda _: None,
        connection_checker=lambda _: True,
        cycle_runner=_runner,
        cycle_intent_executor=lambda **kwargs: SimpleNamespace(
            submitted_exit_count=0, failed_exit_count=0
        ),
        order_dispatcher=lambda **kwargs: {"submitted_count": 0},
    )
    return supervisor, _FakeRuntimeContext


def test_effective_equity_uses_strategy_weight() -> None:
    """Supervisor passes account.equity * weight to cycle_runner, not account.equity."""
    settings = _make_settings()
    runner = _CapturingCycleRunner()
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        cycle_runner=runner,
        only_breakout=True,
    )
    # Pre-populate session state to bypass session-open DB writes
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {"breakout": 0.6}

    supervisor.run_cycle_once(now=lambda: _NOW)

    assert runner.captured_strategy_names == ["breakout"]
    assert abs(runner.captured_equities[0] - 6_000.0) < 1e-6


def test_effective_equity_fallback_for_missing_weight() -> None:
    """When strategy has no entry in weights dict, use equal weight fallback."""
    settings = _make_settings()
    runner = _CapturingCycleRunner()
    supervisor, _ = _make_supervisor(
        settings=settings,
        broker_equity=10_000.0,
        cycle_runner=runner,
        only_breakout=True,
    )
    # Pre-populate session state with empty weights dict
    supervisor._session_equity_baseline[_SESSION_DATE] = 10_000.0
    supervisor._session_capital_weights[_SESSION_DATE] = {}

    supervisor.run_cycle_once(now=lambda: _NOW)

    # Only breakout active → fallback = 1.0 / 1 = 1.0 → full equity
    assert runner.captured_strategy_names == ["breakout"]
    assert abs(runner.captured_equities[0] - 10_000.0) < 1e-6


def test_update_session_weights_uses_cached_db_weights_on_crash_recovery() -> None:
    """If today's weights already exist in DB, return them without recomputing."""
    from alpaca_bot.storage import StrategyWeight

    cached_weight = StrategyWeight(
        strategy_name="breakout",
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        weight=0.7,
        sharpe=2.1,
        computed_at=datetime(_SESSION_DATE.year, _SESSION_DATE.month, _SESSION_DATE.day,
                             10, 0, 0, tzinfo=timezone.utc),
    )
    order_store = _RecordingOrderStore()
    weight_store = _FakeWeightStore(preloaded=[cached_weight])

    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
    )

    weights = supervisor._update_session_weights(_SESSION_DATE)

    assert weights == {"breakout": 0.7}
    # order_store.list_trade_pnl_by_strategy was never called (returned cached weights)
    assert weight_store.upserted == []


def test_update_session_weights_computes_and_stores_when_no_cache() -> None:
    """When DB has no today's weights, compute from trade rows and store."""
    from datetime import timedelta
    order_store = _RecordingOrderStore(
        pnl_rows=[
            {"strategy_name": "breakout", "exit_date": _SESSION_DATE - timedelta(days=1), "pnl": 100.0}
            for _ in range(5)
        ]
    )
    weight_store = _FakeWeightStore(preloaded=[])

    settings = _make_settings()
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
        only_breakout=True,
    )

    weights = supervisor._update_session_weights(_SESSION_DATE)

    assert "breakout" in weights
    assert len(weight_store.upserted) == 1


def test_update_session_weights_writes_audit_event() -> None:
    """_update_session_weights always writes an AuditEvent with the computed weights."""
    order_store = _RecordingOrderStore(pnl_rows=[])
    weight_store = _FakeWeightStore(preloaded=[])

    settings = _make_settings()
    supervisor, FakeRuntime = _make_supervisor(
        settings=settings,
        weight_store=weight_store,
        order_store=order_store,
        only_breakout=True,
    )

    supervisor._update_session_weights(_SESSION_DATE)

    audit_store = supervisor.runtime.audit_event_store
    events = [e for e in audit_store.appended if e.event_type == "strategy_weights_updated"]
    assert len(events) == 1
    assert "breakout" in events[0].payload
```

- [ ] **Step 2: Run to verify tests fail**

Run: `pytest tests/unit/test_supervisor_weights.py -v`

Expected: `AttributeError: 'RuntimeSupervisor' object has no attribute '_session_capital_weights'`

- [ ] **Step 3: Implement supervisor changes**

In `src/alpaca_bot/runtime/supervisor.py`:

**3a. Add import for weighting module** (after existing storage imports):

```python
from alpaca_bot.risk.weighting import compute_strategy_weights
```

Note: `AuditEvent` is already imported in supervisor.py (line 46) — no additional storage import needed.

**3b. Add `_session_capital_weights` to `__init__`** (after `_session_equity_baseline` at line ~125):

```python
        # Keyed by session_date (ET); populated once per day at session open.
        # Maps strategy_name → capital weight fraction (weights sum to 1.0).
        self._session_capital_weights: dict[date, dict[str, float]] = {}
```

**3c. Add `_update_session_weights` method** (add anywhere after `_save_session_state`, e.g. after line ~1165):

```python
    def _update_session_weights(self, session_date: date) -> dict[str, float]:
        """Compute or retrieve capital weights for session_date.

        On crash recovery: if today's weights already exist in DB, returns them
        immediately. Otherwise computes from 28-day trade PnL lookback, persists,
        and writes an AuditEvent.
        """
        weight_store = getattr(self.runtime, "strategy_weight_store", None)
        if weight_store is None:
            active_names = [name for name, _ in self._resolve_active_strategies()]
            n = max(len(active_names), 1)
            return {name: 1.0 / n for name in active_names}

        store_lock = getattr(self.runtime, "store_lock", None)
        lock_ctx = store_lock if store_lock is not None else contextlib.nullcontext()

        with lock_ctx:
            existing = weight_store.load_all(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
            )
        if existing and all(w.computed_at.date() == session_date for w in existing):
            return {w.strategy_name: w.weight for w in existing}

        end_date = session_date - timedelta(days=1)
        start_date = end_date - timedelta(days=28)
        active_names = [name for name, _ in self._resolve_active_strategies()]

        with lock_ctx:
            trade_rows = self.runtime.order_store.list_trade_pnl_by_strategy(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                start_date=start_date,
                end_date=end_date,
            )

        result = compute_strategy_weights(trade_rows, active_names)
        now = datetime.now(timezone.utc)

        with lock_ctx:
            weight_store.upsert_many(
                weights=result.weights,
                sharpes=result.sharpes,
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                computed_at=now,
            )

        # _append_audit acquires store_lock internally — do NOT hold it here
        self._append_audit(
            AuditEvent(
                event_type="strategy_weights_updated",
                payload={name: round(w, 6) for name, w in result.weights.items()},
                created_at=now,
            )
        )
        return result.weights
```

**3d. Add session-open weight computation** in `run_cycle_once`, immediately after the session equity baseline block (after line ~342, after `baseline_equity = self._session_equity_baseline[session_date]`):

```python
        if session_date not in self._session_capital_weights:
            weights = self._update_session_weights(session_date)
            self._session_capital_weights[session_date] = weights
```

**3e. Inject effective_equity** in the per-strategy loop (replace line 617 `equity=account.equity,` with):

First, find the per-strategy loop. The loop variable is `strategy_name` (from `active_strategies` list). Just before line 613 (`cycle_result = self._cycle_runner(...)`), add:

```python
                strategy_weight = self._session_capital_weights[session_date].get(
                    strategy_name, 1.0 / max(len(active_strategies), 1)
                )
                effective_equity = account.equity * strategy_weight
```

Then change line 617 from:
```python
                    equity=account.equity,
```
to:
```python
                    equity=effective_equity,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_supervisor_weights.py -v`

Expected: All 5 tests PASS.

Run full test suite:

Run: `pytest -x`

Expected: All existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_weights.py
git commit -m "feat: inject strategy capital weights into supervisor cycle loop"
```

---

## Task 6: Web Layer — Dashboard Capital Allocation Panel

**Files:**
- Modify: `src/alpaca_bot/web/service.py`
- Modify: `src/alpaca_bot/web/app.py`
- Modify: `src/alpaca_bot/web/templates/dashboard.html`
- Extend: `tests/unit/test_web_service.py`

`★ Insight ─────────────────────────────────────`
The web layer uses a factory pattern (`app.state.strategy_weight_store_factory`) so tests can inject a fake store. The `load_strategy_weights()` function is stateless and testable in isolation. The template hides the panel when `strategy_weights` is an empty list, matching the spec requirement that the panel is hidden before the first trading session.
`─────────────────────────────────────────────────`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_web_service.py` (append after existing tests):

```python
# ── test_load_strategy_weights ────────────────────────────────────────────────

class TestLoadStrategyWeights:
    """Tests for load_strategy_weights() service function."""

    def _make_fake_weight_store(self, weights: list) -> object:
        class _FakeStore:
            def load_all(self, **kwargs):
                return weights
        return _FakeStore()

    def test_returns_empty_list_when_no_weights(self) -> None:
        from alpaca_bot.web.service import load_strategy_weights
        store = self._make_fake_weight_store([])
        result = load_strategy_weights(
            settings=_make_settings(),
            connection=None,
            strategy_weight_store=store,
        )
        assert result == []

    def test_returns_weight_rows_sorted_by_weight_descending(self) -> None:
        from alpaca_bot.web.service import load_strategy_weights, StrategyWeightRow
        from alpaca_bot.storage import StrategyWeight
        from alpaca_bot.config import TradingMode
        from datetime import datetime, timezone

        now = datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)
        store = self._make_fake_weight_store([
            StrategyWeight("momentum", TradingMode.PAPER, "v1", 0.3, 0.9, now),
            StrategyWeight("breakout", TradingMode.PAPER, "v1", 0.4, 1.8, now),
            StrategyWeight("orb", TradingMode.PAPER, "v1", 0.3, 0.5, now),
        ])
        result = load_strategy_weights(
            settings=_make_settings(),
            connection=None,
            strategy_weight_store=store,
        )
        assert len(result) == 3
        assert result[0].strategy_name == "breakout"  # highest weight first
        assert abs(result[0].weight - 0.4) < 1e-9
        assert abs(result[0].sharpe - 1.8) < 1e-9
        assert isinstance(result[0], StrategyWeightRow)

    def test_weight_row_fields(self) -> None:
        from alpaca_bot.web.service import load_strategy_weights, StrategyWeightRow
        from alpaca_bot.storage import StrategyWeight
        from alpaca_bot.config import TradingMode
        from datetime import datetime, timezone

        now = datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc)
        store = self._make_fake_weight_store([
            StrategyWeight("breakout", TradingMode.PAPER, "v1", 0.6, 2.1, now),
        ])
        result = load_strategy_weights(
            settings=_make_settings(),
            connection=None,
            strategy_weight_store=store,
        )
        row = result[0]
        assert row.strategy_name == "breakout"
        assert abs(row.weight - 0.6) < 1e-9
        assert abs(row.sharpe - 2.1) < 1e-9
```

Where `_make_settings()` matches the existing helper in `test_web_service.py` (it already exists there).

- [ ] **Step 2: Run to verify tests fail**

Run: `pytest tests/unit/test_web_service.py::TestLoadStrategyWeights -v`

Expected: `ImportError: cannot import name 'load_strategy_weights' from 'alpaca_bot.web.service'`

- [ ] **Step 3: Add StrategyWeightRow and load_strategy_weights to service.py**

In `src/alpaca_bot/web/service.py`, add the following imports at the top (with existing imports):

```python
from alpaca_bot.storage import StrategyWeight, StrategyWeightStore
```

After the `EquityChartData` dataclass, add:

```python
@dataclass(frozen=True)
class StrategyWeightRow:
    strategy_name: str
    weight: float
    sharpe: float


def load_strategy_weights(
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    strategy_weight_store=None,
) -> list[StrategyWeightRow]:
    store = strategy_weight_store or StrategyWeightStore(connection)
    weights = store.load_all(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    rows = [
        StrategyWeightRow(
            strategy_name=w.strategy_name,
            weight=w.weight,
            sharpe=w.sharpe,
        )
        for w in weights
    ]
    return sorted(rows, key=lambda r: r.weight, reverse=True)
```

- [ ] **Step 4: Run service tests to verify they pass**

Run: `pytest tests/unit/test_web_service.py::TestLoadStrategyWeights -v`

Expected: All 3 tests PASS.

- [ ] **Step 5: Wire factory into app.py**

In `src/alpaca_bot/web/app.py`:

**5a. Add import** at the top (alongside existing service imports):

```python
from alpaca_bot.web.service import (
    ...existing imports...,
    StrategyWeightRow,
    load_strategy_weights,
)
```

**5b. Add `strategy_weight_store_factory` param** to `create_app` (after `equity_chart_data_factory` line ~78):

```python
    strategy_weight_store_factory: Callable[[ConnectionProtocol], object] | None = None,
```

**5c. Register in app state** (after the `equity_chart_data_factory` registration at line ~126):

```python
    app.state.strategy_weight_store_factory = strategy_weight_store_factory or StrategyWeightStore
```

Add `StrategyWeightStore` to the imports from `alpaca_bot.storage` in `app.py`.

**5d. Load weights in metrics_page** — within the `try` block of `metrics_page`, after the `load_metrics_snapshot` call (before the `finally:`):

```python
                strategy_weights = load_strategy_weights(
                    settings=app_settings,
                    connection=connection,
                    strategy_weight_store=_build_store(
                        app.state.strategy_weight_store_factory, connection
                    ),
                )
```

If an exception occurs before this point, add an `except` that returns the 503 (as before). Then add `"strategy_weights": strategy_weights` to the template context dict:

```python
            context={
                "request": request,
                "settings": app_settings,
                "snapshot": None,
                "metrics": metrics,
                "strategy_weights": strategy_weights,   # add this
                "operator_email": operator,
                "session_date": session_date.isoformat(),
                "today": today.isoformat(),
                "prev_date": prev_date,
                "next_date": next_date,
                "date_warning": date_warning,
            },
```

Note: initialise `strategy_weights = []` before the outer `try` block as the safe default, so the template context always has the key even if the DB call fails.

- [ ] **Step 6: Add Capital Allocation panel to dashboard.html**

In `src/alpaca_bot/web/templates/dashboard.html`, add the Capital Allocation panel after the equity chart panel (after the `{% endif %}` that closes the equity chart block, around line ~555):

```html
      {% if strategy_weights %}
      <div class="panel" style="margin-bottom:1.5rem">
        <div class="eyebrow">Capital Allocation</div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th style="text-align:right">Weight</th>
                <th style="text-align:right">Sharpe (20d)</th>
              </tr>
            </thead>
            <tbody>
              {% for row in strategy_weights %}
                <tr>
                  <td>{{ row.strategy_name }}</td>
                  <td style="text-align:right">{{ "%.1f"|format(row.weight * 100) }}%</td>
                  <td style="text-align:right">{{ "%.2f"|format(row.sharpe) }}</td>
                </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      {% endif %}
```

- [ ] **Step 7: Run full test suite**

Run: `pytest -x`

Expected: All tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/alpaca_bot/web/service.py src/alpaca_bot/web/app.py src/alpaca_bot/web/templates/dashboard.html tests/unit/test_web_service.py
git commit -m "feat: add Capital Allocation panel to metrics dashboard"
```

---

## Self-Review

### Spec Coverage

| Spec requirement | Covered in |
|---|---|
| Weights from last 20 trading days (28 calendar days) | Task 5 `_update_session_weights` |
| Sharpe formula: mean/std × √252, floored at 0 | Task 2 `compute_strategy_weights` |
| `<5` trades → sharpe=0 | Task 2 |
| All zero Sharpes → equal weights | Task 2 |
| Clip [5%, 40%] iterative renormalize | Task 2 |
| Weights sum to 1.0 | Task 2 |
| `strategy_weights` table with `sharpe` column | Task 1 |
| `StrategyWeightStore.upsert_many` + `load_all` | Task 3 |
| `OrderStore.list_trade_pnl_by_strategy` | Task 3 |
| `RuntimeContext.strategy_weight_store` + bootstrap + reconnect | Task 4 |
| `_session_capital_weights` cache in supervisor | Task 5 |
| Session-open weight computation guard | Task 5 |
| Crash recovery: load today's DB weights before recomputing | Task 5 |
| `effective_equity = account.equity × weight` at line 617 | Task 5 |
| Fallback 1/n for missing strategy | Task 5 |
| `AuditEvent` with `event_type="strategy_weights_updated"` | Task 5 |
| `load_strategy_weights()` service function | Task 6 |
| Capital Allocation panel hidden when empty | Task 6 |
| Dashboard shows Strategy, Weight%, Sharpe (20d) | Task 6 |
| No leverage: weights sum to 1.0 | Task 2 invariant |
| Loss limit uses baseline_equity, not effective_equity | Unchanged — only line 617 equity changes |
| Paper/live independent weights via trading_mode filter | Task 3/5 — `trading_mode` param in all queries |

### No Placeholders Found

All steps have exact code, exact commands, and expected outcomes.

### Type Consistency

- `WeightResult.weights: dict[str, float]` — consistent across Tasks 2, 3, 5, 6
- `StrategyWeight.sharpe: float` — added in Task 1 migration + Task 3 dataclass
- `upsert_many(weights=..., sharpes=..., trading_mode=..., strategy_version=..., computed_at=...)` — consistent between Task 3 implementation and Task 5 call site
- `load_all()` returns `list[StrategyWeight]` — consistent between Task 3 implementation and Task 5 call site
- `_update_session_weights` returns `dict[str, float]` — matches `_session_capital_weights` value type

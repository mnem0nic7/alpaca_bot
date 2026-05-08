# Option PnL → Sharpe Weighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Feed closed option trade PnL into the Sharpe/weighting pipeline so that option strategies earn confidence scores from their actual win/loss history rather than perpetually trading at `CONFIDENCE_FLOOR`.

**Architecture:** Add `OptionOrderRepository.list_trade_pnl_by_strategy` that queries `option_orders` with the same correlated-subquery pattern as the equity version, then merge its output at the two supervisor call sites (`_update_session_weights` and the losing-streak detection block) before passing all rows to `compute_strategy_weights` / `compute_losing_day_streaks`. No changes to the weighting algorithm.

**Tech Stack:** Python, psycopg2-style cursor, pytest with fake callables (no mocks of own classes).

---

## Files

| Action | Path | What changes |
|--------|------|-------------|
| Modify | `src/alpaca_bot/storage/repositories.py` | Add `OptionOrderRepository.list_trade_pnl_by_strategy` after `list_open_option_positions` |
| Modify | `src/alpaca_bot/runtime/supervisor.py` | Merge option rows at lines ~372 (streak) and ~1413 (weights) |
| Create | `tests/unit/test_option_order_repository_pnl.py` | 5 unit tests for the new repository method |
| Modify | `tests/unit/test_supervisor_weights.py` | Add `test_option_pnl_feeds_into_sharpe` and extend `_make_supervisor` |

---

### Task 1: Add `OptionOrderRepository.list_trade_pnl_by_strategy`

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (after `list_open_option_positions`, line ~1763)
- Create: `tests/unit/test_option_order_repository_pnl.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_option_order_repository_pnl.py`:

```python
from __future__ import annotations

from datetime import date

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.repositories import OptionOrderRepository


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.last_params: tuple | None = None

    def execute(self, query, params=None):
        self.last_params = params

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _repo(rows=None) -> tuple[OptionOrderRepository, _FakeCursor]:
    cursor = _FakeCursor(rows or [])

    class _Conn:
        def commit(self): pass
        def rollback(self): pass
        def cursor(self): return cursor

    return OptionOrderRepository(_Conn()), cursor


def test_returns_empty_when_no_closed_sells():
    repo, _ = _repo(rows=[])
    result = repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    assert result == []


def test_returns_correct_pnl_for_matched_buy_sell():
    # row: (strategy_name, exit_date, qty, exit_fill, entry_fill)
    rows = [("breakout_calls", date(2026, 4, 1), 3, 3.50, 2.00)]
    repo, _ = _repo(rows=rows)
    result = repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    assert len(result) == 1
    r = result[0]
    assert r["strategy_name"] == "breakout_calls"
    assert r["exit_date"] == date(2026, 4, 1)
    # (3.50 - 2.00) * 3 * 100 = 450.0
    assert abs(r["pnl"] - 450.0) < 1e-6


def test_excludes_unmatched_sells():
    # entry_fill is None → no correlated buy fill → must be excluded
    rows = [("breakout_calls", date(2026, 4, 1), 3, 3.50, None)]
    repo, _ = _repo(rows=rows)
    result = repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    assert result == []


def test_respects_date_range():
    # Verify start_date and end_date are passed as SQL params
    rows = []
    repo, cursor = _repo(rows=rows)
    repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        start_date=date(2026, 3, 1),
        end_date=date(2026, 4, 30),
    )
    params = cursor.last_params
    assert date(2026, 3, 1) in params, "start_date must be passed as SQL param"
    assert date(2026, 4, 30) in params, "end_date must be passed as SQL param"


def test_respects_trading_mode_and_strategy_version():
    rows = []
    repo, cursor = _repo(rows=rows)
    repo.list_trade_pnl_by_strategy(
        trading_mode=TradingMode.LIVE,
        strategy_version="v2",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 5, 7),
    )
    params = cursor.last_params
    assert "live" in params, "trading_mode value must be passed as SQL param"
    assert "v2" in params, "strategy_version must be passed as SQL param"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_order_repository_pnl.py -v
```

Expected: `AttributeError: 'OptionOrderRepository' object has no attribute 'list_trade_pnl_by_strategy'`

- [ ] **Step 3: Add `list_trade_pnl_by_strategy` to `OptionOrderRepository`**

In `src/alpaca_bot/storage/repositories.py`, after `list_open_option_positions` (line ~1763) and before `load_by_broker_order_id`, insert:

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
        """Return one dict per closed option trade in the date range with strategy attribution.

        Each dict: {strategy_name: str, exit_date: date, pnl: float}
        pnl = (sell_fill_price - buy_fill_price) * qty * 100
        Rows where the correlated buy has no fill_price are excluded.
        """
        rows = fetch_all(
            self._connection,
            """
            SELECT x.strategy_name,
                   DATE(x.updated_at AT TIME ZONE %s) AS exit_date,
                   COALESCE(x.filled_quantity, x.quantity) AS qty,
                   x.fill_price AS exit_fill,
                   (SELECT e.fill_price
                      FROM option_orders e
                     WHERE e.occ_symbol = x.occ_symbol
                       AND e.trading_mode = x.trading_mode
                       AND e.strategy_version = x.strategy_version
                       AND e.strategy_name IS NOT DISTINCT FROM x.strategy_name
                       AND e.side = 'buy'
                       AND e.fill_price IS NOT NULL
                       AND e.status = 'filled'
                       AND e.updated_at <= x.updated_at
                     ORDER BY e.updated_at DESC
                     LIMIT 1) AS entry_fill
              FROM option_orders x
             WHERE x.trading_mode = %s
               AND x.strategy_version = %s
               AND x.side = 'sell'
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
                "pnl": (float(row[3]) - float(row[4])) * float(row[2]) * 100,
            }
            for row in rows
            if row[4] is not None
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_option_order_repository_pnl.py -v
```

Expected: 5 tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_option_order_repository_pnl.py
git commit -m "feat: add OptionOrderRepository.list_trade_pnl_by_strategy for Sharpe weighting"
```

---

### Task 2: Merge option PnL at both supervisor call sites

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py` (lines ~372 and ~1413)
- Modify: `tests/unit/test_supervisor_weights.py` (extend `_make_supervisor` + add test)

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_supervisor_weights.py`, add `_RecordingOptionOrderStore` and extend `_make_supervisor`, then add the new test.

After the existing `_RecordingOrderStore` class definition (~line 61), add:

```python
class _RecordingOptionOrderStore:
    def __init__(self, *, pnl_rows: list[dict] | None = None):
        self._pnl_rows = pnl_rows or []

    def list_trade_pnl_by_strategy(self, **kwargs) -> list[dict]:
        return list(self._pnl_rows)
```

In `_make_supervisor`, add `option_order_store` parameter and wire it into `_FakeRuntimeContext`. The current signature is:

```python
def _make_supervisor(
    *,
    settings: Settings,
    broker_equity: float = 10_000.0,
    weight_store: _FakeWeightStore | None = None,
    order_store: _RecordingOrderStore | None = None,
    cycle_runner=None,
    only_breakout: bool = True,
    order_dispatcher=None,
):
```

Change to:

```python
def _make_supervisor(
    *,
    settings: Settings,
    broker_equity: float = 10_000.0,
    weight_store: _FakeWeightStore | None = None,
    order_store: _RecordingOrderStore | None = None,
    option_order_store: _RecordingOptionOrderStore | None = None,
    cycle_runner=None,
    only_breakout: bool = True,
    order_dispatcher=None,
):
```

Inside `_make_supervisor`, change `_FakeRuntimeContext` to include `option_order_store`:

```python
    _order_store = order_store or _RecordingOrderStore()
    _weight_store = weight_store
    _option_order_store = option_order_store

    class _FakeRuntimeContext:
        connection = _FakeConn()
        store_lock = None
        order_store = _order_store
        strategy_weight_store = _weight_store
        option_order_store = _option_order_store  # ADD THIS LINE
        trading_status_store = _FakeTradingStatusStore()
        position_store = _FakePositionStore()
        daily_session_state_store = _FakeSessionStateStore()
        audit_event_store = _RecordingAuditStore()
        strategy_flag_store = _FakeStrategyFlagStore()
        watchlist_store = _FakeWatchlistStore()
        def commit(self): pass
```

Then add the new test at the end of the file:

```python
def test_option_pnl_feeds_into_sharpe() -> None:
    """Option strategies with closed profitable trades produce non-zero Sharpe via _update_session_weights."""
    # 6 profitable trades for breakout_calls on distinct dates (min_trades=5 threshold)
    option_rows = [
        {"strategy_name": "breakout_calls", "exit_date": date(2026, 1, d), "pnl": 150.0}
        for d in range(1, 7)
    ]
    settings = _make_settings(ENABLE_OPTIONS_TRADING="true")
    supervisor, _ = _make_supervisor(
        settings=settings,
        weight_store=_FakeWeightStore(preloaded=[]),
        order_store=_RecordingOrderStore(pnl_rows=[]),
        option_order_store=_RecordingOptionOrderStore(pnl_rows=option_rows),
        only_breakout=False,
    )
    result = supervisor._update_session_weights(_SESSION_DATE)
    assert result.sharpes.get("breakout_calls", 0.0) > 0.0, (
        "breakout_calls must earn a positive Sharpe when it has 6 profitable closed option trades"
    )
```

- [ ] **Step 2: Run the new test to verify it fails**

```bash
pytest tests/unit/test_supervisor_weights.py::test_option_pnl_feeds_into_sharpe -v
```

Expected: FAIL — `breakout_calls` sharpe is 0.0 (option rows not yet merged)

- [ ] **Step 3: Merge option rows at the losing-streak call site (line ~372)**

In `src/alpaca_bot/runtime/supervisor.py`, find the block starting at `_streak_lock = getattr(...)` (~line 370). Replace:

```python
            _streak_lock = getattr(self.runtime, "store_lock", None)
            with _streak_lock if _streak_lock is not None else contextlib.nullcontext():
                _streak_rows = self.runtime.order_store.list_trade_pnl_by_strategy(
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    start_date=date(2000, 1, 1),
                    end_date=session_date - timedelta(days=1),
                )
            _streaks = compute_losing_day_streaks(_streak_rows, list(session_sharpes.keys()))
```

With:

```python
            _streak_lock = getattr(self.runtime, "store_lock", None)
            with _streak_lock if _streak_lock is not None else contextlib.nullcontext():
                _streak_rows = self.runtime.order_store.list_trade_pnl_by_strategy(
                    trading_mode=self.settings.trading_mode,
                    strategy_version=self.settings.strategy_version,
                    start_date=date(2000, 1, 1),
                    end_date=session_date - timedelta(days=1),
                )
                _opt_store = getattr(self.runtime, "option_order_store", None)
                _opt_streak_rows: list[dict] = []
                if _opt_store is not None:
                    try:
                        _opt_streak_rows = _opt_store.list_trade_pnl_by_strategy(
                            trading_mode=self.settings.trading_mode,
                            strategy_version=self.settings.strategy_version,
                            start_date=date(2000, 1, 1),
                            end_date=session_date - timedelta(days=1),
                        )
                    except Exception:
                        logger.warning(
                            "Failed to fetch option PnL rows for streak detection; excluding",
                            exc_info=True,
                        )
            _streaks = compute_losing_day_streaks(
                _streak_rows + _opt_streak_rows, list(session_sharpes.keys())
            )
```

- [ ] **Step 4: Merge option rows at the Sharpe/weight call site (line ~1413)**

In `src/alpaca_bot/runtime/supervisor.py`, find `_update_session_weights`. Replace:

```python
        with lock_ctx:
            trade_rows = self.runtime.order_store.list_trade_pnl_by_strategy(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                start_date=start_date,
                end_date=end_date,
            )

        result = compute_strategy_weights(trade_rows, active_names)
```

With:

```python
        with lock_ctx:
            trade_rows = self.runtime.order_store.list_trade_pnl_by_strategy(
                trading_mode=self.settings.trading_mode,
                strategy_version=self.settings.strategy_version,
                start_date=start_date,
                end_date=end_date,
            )
            _opt_store = getattr(self.runtime, "option_order_store", None)
            option_trade_rows: list[dict] = []
            if _opt_store is not None:
                try:
                    option_trade_rows = _opt_store.list_trade_pnl_by_strategy(
                        trading_mode=self.settings.trading_mode,
                        strategy_version=self.settings.strategy_version,
                        start_date=start_date,
                        end_date=end_date,
                    )
                except Exception:
                    logger.warning(
                        "Failed to fetch option PnL rows; excluding from Sharpe computation",
                        exc_info=True,
                    )

        result = compute_strategy_weights(trade_rows + option_trade_rows, active_names)
```

- [ ] **Step 5: Run the new test to verify it passes**

```bash
pytest tests/unit/test_supervisor_weights.py::test_option_pnl_feeds_into_sharpe -v
```

Expected: PASS

- [ ] **Step 6: Run the full test suite**

```bash
pytest
```

Expected: all tests pass — existing supervisor tests that don't wire `option_order_store` are unaffected because `getattr(self.runtime, "option_order_store", None)` returns `None` when the attribute is absent.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_weights.py
git commit -m "feat: merge option trade PnL into Sharpe weighting and streak detection"
```

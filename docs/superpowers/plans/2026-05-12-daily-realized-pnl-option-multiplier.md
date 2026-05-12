# daily_realized_pnl Option ×100 Multiplier Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the ×100 option contract multiplier to three `OrderStore` PnL functions in `repositories.py` so that the supervisor's portfolio loss limit, per-symbol loss limit, and losing-streak detection all compute correct dollar amounts for option trades.

**Architecture:** A private `_contract_multiplier(strategy_name: str | None) -> int` helper (returns 100 for `"option"`, 1 otherwise) is added to `repositories.py`. `daily_realized_pnl` and `daily_realized_pnl_by_symbol` each gain `x.strategy_name` in their SQL SELECT (new row[4]), and their Python aggregation applies the multiplier. `list_trade_pnl_by_strategy` already has `strategy_name` in row[0]; only its Python dict comprehension changes.

**Tech Stack:** Python, psycopg2-style fake-connection test pattern, pytest.

---

## File Map

| File | Change |
|---|---|
| `src/alpaca_bot/storage/repositories.py` | Add `_contract_multiplier` before `OrderStore`; modify 3 PnL methods |
| `tests/unit/test_storage_db.py` | Update 7 existing rows; add 3+4+1 = 8 new tests |

---

## Background: test harness

Every test in this plan uses the existing `_make_fake_connection(rows)` helper (defined at line 201 of `test_storage_db.py`). It returns a stub whose `cursor().fetchall()` returns whatever list of tuples you pass in as `rows`. No real database is needed.

The `OrderStore` stores a reference to this fake connection and passes it to `fetch_all`. Since `fetch_all` calls `cursor().execute(...)` then `cursor().fetchall()`, the rows you provide are returned verbatim.

---

## Task 1 — `_contract_multiplier` helper + `daily_realized_pnl` fix

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (lines 243–244 and 447–499)
- Modify: `tests/unit/test_storage_db.py` (`TestDailyRealizedPnl` class, lines 221–314)

### Context

`daily_realized_pnl` currently SELECTs 4 columns: `symbol` (row[0]), `entry_fill` (row[1]), `exit_fill` (row[2]), `qty` (row[3]). The Python sum uses only those four. The fix adds `x.strategy_name` as row[4] to the SQL and applies `_contract_multiplier(row[4])` in the Python aggregation.

The existing 7 test rows are 4-tuples. They must become 5-tuples (with `"breakout"` appended as row[4]) so they remain correct after the SQL change. Passing 5-tuples to the *old* implementation is safe: the old code only accesses row[0]–row[3].

- [ ] **Step 1: Update the 7 existing test rows to 5-tuples and add 3 failing option tests**

In `tests/unit/test_storage_db.py`, replace the entire `TestDailyRealizedPnl` class (lines 221–314) with:

```python
class TestDailyRealizedPnl:
    SESSION_DATE = date(2026, 4, 25)
    MODE = TradingMode.PAPER
    STRATEGY = "v1-breakout"

    def _store(self, rows: list[tuple]) -> OrderStore:
        return OrderStore(_make_fake_connection(rows))

    def test_single_profitable_trade_returns_correct_pnl(self):
        """(exit_fill - entry_fill) × qty for a single winner."""
        # symbol, entry_fill, exit_fill, qty, strategy_name
        rows = [("AAPL", 150.00, 155.00, 10, "breakout")]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(50.00)

    def test_single_losing_trade_returns_negative_pnl(self):
        """Negative PnL when exit is below entry."""
        rows = [("AAPL", 155.00, 150.00, 10, "breakout")]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-50.00)

    def test_two_symbols_sums_both_trades(self):
        """PnL from two different symbols is summed."""
        rows = [
            ("AAPL", 150.00, 155.00, 10, "breakout"),   # +50
            ("MSFT", 400.00, 395.00, 5,  "breakout"),   # -25
        ]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(25.00)

    def test_partial_fill_uses_filled_quantity_not_order_quantity(self):
        """qty column is COALESCE(filled_quantity, quantity)."""
        rows = [("AAPL", 150.00, 156.00, 7, "breakout")]  # partial fill of 7 shares
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(42.00)

    def test_no_trades_returns_zero(self):
        """When there are no completed trades, PnL must be 0.0."""
        store = self._store([])
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == 0.0

    def test_exit_with_null_entry_fill_treated_as_full_loss(self):
        """Rows where entry_fill is None must be counted as -(exit_fill × qty × multiplier)."""
        rows = [
            ("AAPL", None,   155.00, 10, "breakout"),   # no entry fill → -(155 × 10) = -1550
            ("MSFT", 400.00, 405.00,  5, "breakout"),   # +25
        ]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-1525.00)

    def test_all_exits_null_entry_fill_returns_total_full_loss(self):
        """When every row lacks an entry fill the entire session P&L is negative."""
        rows = [
            ("AAPL", None, 100.00, 5, "breakout"),   # -(100 × 5) = -500
            ("MSFT", None, 200.00, 3, "breakout"),   # -(200 × 3) = -600
        ]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-1100.00)

    # ── Option-specific tests (these will FAIL until Task 1 implementation) ──

    def test_option_trade_applies_100x_multiplier(self):
        """Option exit applies ×100: (0.80 - 1.20) × 2 × 100 = -80.0."""
        rows = [("AAPL", 1.20, 0.80, 2, "option")]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-80.0)

    def test_mixed_equity_and_option_sums_correctly(self):
        """Equity and option rows are each multiplied by their own factor."""
        rows = [
            ("MSFT", 150.0, 155.0, 10, "breakout"),   # (5) × 10 × 1  = +50
            ("AAPL", 1.20,  0.80,   2, "option"),     # (-0.4) × 2 × 100 = -80
        ]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-30.0)

    def test_option_null_entry_fill_fail_safe_also_multiplied(self):
        """Fail-safe path for options also applies ×100: -(1.20 × 2 × 100) = -240.0."""
        rows = [("AAPL", None, 1.20, 2, "option")]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(-240.0)
```

- [ ] **Step 2: Run the tests to verify the 7 existing pass and the 3 option tests fail**

```bash
pytest tests/unit/test_storage_db.py::TestDailyRealizedPnl -v
```

Expected: 7 tests PASS (existing behavior unchanged), 3 tests FAIL (option multiplier not yet applied — each will show the wrong value, e.g. `-0.8` instead of `-80.0`).

- [ ] **Step 3: Add `_contract_multiplier` before `OrderStore` in repositories.py**

In `src/alpaca_bot/storage/repositories.py`, after the `_row_to_order_record` function (around line 243) and immediately before `class OrderStore:` (line 246), insert:

```python
def _contract_multiplier(strategy_name: str | None) -> int:
    return 100 if strategy_name == "option" else 1
```

- [ ] **Step 4: Fix `daily_realized_pnl` — add `x.strategy_name` to SQL SELECT**

In `src/alpaca_bot/storage/repositories.py`, inside `daily_realized_pnl`, replace the `f"""` SQL string so the SELECT ends with `, x.strategy_name` before `FROM orders x`. The full updated SQL block (rows parameter stays unchanged):

```python
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                x.symbol,
                (
                    SELECT e.fill_price
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
                    LIMIT 1
                ) AS entry_fill,
                x.fill_price AS exit_fill,
                COALESCE(x.filled_quantity, x.quantity) AS qty,
                x.strategy_name
            FROM orders x
            WHERE x.trading_mode = %s
              AND x.strategy_version = %s
              AND x.intent_type IN ('stop', 'exit')
              AND x.fill_price IS NOT NULL
              AND x.status = 'filled'
              AND DATE(x.updated_at AT TIME ZONE %s) = %s
              {strategy_clause}
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                session_date,
                *strategy_params,
            ),
        )
```

Row layout after change: `(symbol:0, entry_fill:1, exit_fill:2, qty:3, strategy_name:4)`.

- [ ] **Step 5: Fix `daily_realized_pnl` — update Python sum to apply multiplier**

Replace the `missing_entry` check and `return sum(...)` block (currently lines 485–499) with:

```python
        missing_entry = [row for row in rows if row[1] is None]
        if missing_entry:
            logger.error(
                "daily_realized_pnl: %d exit row(s) have no correlated entry fill "
                "(symbols: %s); treating as full loss to fail safe on loss-limit check",
                len(missing_entry),
                [row[0] for row in missing_entry],
            )
        return sum(
            _contract_multiplier(row[4]) * (
                (float(row[2]) - float(row[1])) * float(row[3])
                if row[1] is not None
                else -(float(row[2]) * float(row[3]))
            )
            for row in rows
            if row[2] is not None
        )
```

- [ ] **Step 6: Run tests to verify all 10 pass**

```bash
pytest tests/unit/test_storage_db.py::TestDailyRealizedPnl -v
```

Expected: 10 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_storage_db.py
git commit -m "fix: apply x100 multiplier to option trades in daily_realized_pnl"
```

---

## Task 2 — `daily_realized_pnl_by_symbol` fix

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (lines 501–582)
- Modify: `tests/unit/test_storage_db.py` (add `TestDailyRealizedPnlBySymbol` after `TestDailyRealizedPnl`)

### Context

`daily_realized_pnl_by_symbol` has the same SQL structure as `daily_realized_pnl`. Currently it SELECTs `(symbol:0, entry_fill:1, exit_fill:2, qty:3)`. The fix adds `x.strategy_name` (new row[4]) and applies `_contract_multiplier` in the Python loop. No existing test class covers this function — we create one from scratch.

- [ ] **Step 1: Add `TestDailyRealizedPnlBySymbol` with 4 tests (2 will fail)**

Add this class immediately after the `TestDailyRealizedPnl` class block (before the `# Phase 2` comment) in `tests/unit/test_storage_db.py`:

```python
class TestDailyRealizedPnlBySymbol:
    SESSION_DATE = date(2026, 4, 25)
    MODE = TradingMode.PAPER
    STRATEGY = "v1-breakout"

    def _store(self, rows: list[tuple]) -> OrderStore:
        return OrderStore(_make_fake_connection(rows))

    def test_returns_empty_dict_when_no_rows(self):
        store = self._store([])
        result = store.daily_realized_pnl_by_symbol(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert result == {}

    def test_equity_trade_not_multiplied(self):
        """Equity trade: (155 - 150) × 10 × 1 = 50.0."""
        # symbol, entry_fill, exit_fill, qty, strategy_name
        rows = [("MSFT", 150.0, 155.0, 10, "breakout")]
        store = self._store(rows)
        result = store.daily_realized_pnl_by_symbol(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert result == {"MSFT": pytest.approx(50.0)}

    def test_option_trade_applies_100x_multiplier(self):
        """Option trade: (0.80 - 1.20) × 2 × 100 = -80.0."""
        rows = [("AAPL", 1.20, 0.80, 2, "option")]
        store = self._store(rows)
        result = store.daily_realized_pnl_by_symbol(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert result == {"AAPL": pytest.approx(-80.0)}

    def test_option_null_entry_fill_fail_safe_multiplied(self):
        """Fail-safe for options: -(1.20 × 2 × 100) = -240.0."""
        rows = [("AAPL", None, 1.20, 2, "option")]
        store = self._store(rows)
        result = store.daily_realized_pnl_by_symbol(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert result == {"AAPL": pytest.approx(-240.0)}
```

- [ ] **Step 2: Run the new tests to verify 2 pass and 2 fail**

```bash
pytest tests/unit/test_storage_db.py::TestDailyRealizedPnlBySymbol -v
```

Expected: `test_returns_empty_dict_when_no_rows` and `test_equity_trade_not_multiplied` PASS (old code handles these correctly with 5-tuples since it only accesses row[0]–row[3]). `test_option_trade_applies_100x_multiplier` and `test_option_null_entry_fill_fail_safe_multiplied` FAIL (multiplier not yet applied, returning -0.8 and -2.4 instead of -80.0 and -240.0).

- [ ] **Step 3: Fix `daily_realized_pnl_by_symbol` — add `x.strategy_name` to SQL SELECT**

In `src/alpaca_bot/storage/repositories.py`, inside `daily_realized_pnl_by_symbol`, replace the `f"""` SQL string so the SELECT ends with `, x.strategy_name` before `FROM orders x`:

```python
        rows = fetch_all(
            self._connection,
            f"""
            SELECT
                x.symbol,
                (
                    SELECT e.fill_price
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
                    LIMIT 1
                ) AS entry_fill,
                x.fill_price AS exit_fill,
                COALESCE(x.filled_quantity, x.quantity) AS qty,
                x.strategy_name
            FROM orders x
            WHERE x.trading_mode = %s
              AND x.strategy_version = %s
              AND x.intent_type IN ('stop', 'exit')
              AND x.fill_price IS NOT NULL
              AND x.status = 'filled'
              AND DATE(x.updated_at AT TIME ZONE %s) = %s
              {strategy_clause}
            """,
            (
                trading_mode.value,
                strategy_version,
                market_timezone,
                session_date,
                *strategy_params,
            ),
        )
```

Row layout after change: `(symbol:0, entry_fill:1, exit_fill:2, qty:3, strategy_name:4)`.

- [ ] **Step 4: Fix `daily_realized_pnl_by_symbol` — update Python loop to apply multiplier**

Replace the Python aggregation loop (currently the `result: dict[str, float] = {}` block through `return result`) with:

```python
        missing_entry = [row for row in rows if row[1] is None]
        if missing_entry:
            logger.error(
                "daily_realized_pnl_by_symbol: %d exit row(s) have no correlated entry fill "
                "(symbols: %s); treating as full loss to fail safe on per-symbol loss-limit check",
                len(missing_entry),
                [row[0] for row in missing_entry],
            )
        result: dict[str, float] = {}
        for row in rows:
            if row[2] is None:
                continue
            symbol = row[0]
            entry_fill = row[1]
            exit_fill = float(row[2])
            qty = float(row[3])
            multiplier = _contract_multiplier(row[4])
            pnl = (
                (exit_fill - float(entry_fill)) * qty * multiplier
                if entry_fill is not None
                else -(exit_fill * qty) * multiplier
            )
            result[symbol] = result.get(symbol, 0.0) + pnl
        return result
```

The `missing_entry` logging block must appear immediately after `rows = fetch_all(...)` and before the aggregation loop, matching the current code's ordering.

- [ ] **Step 5: Run tests to verify all 4 pass**

```bash
pytest tests/unit/test_storage_db.py::TestDailyRealizedPnlBySymbol -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
pytest tests/unit/test_storage_db.py -v
```

Expected: All tests in the file PASS.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_storage_db.py
git commit -m "fix: apply x100 multiplier to option trades in daily_realized_pnl_by_symbol"
```

---

## Task 3 — `list_trade_pnl_by_strategy` fix

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py` (line 799 in the dict comprehension)
- Modify: `tests/unit/test_storage_db.py` (`TestListTradePnlByStrategy` class)

### Context

`list_trade_pnl_by_strategy` already has `strategy_name` in row[0] of its SELECT. Only the Python dict comprehension needs updating. Existing test rows are 5-tuples `(strategy_name, exit_date, qty, exit_fill, entry_fill)` — no row format changes required.

- [ ] **Step 1: Add a failing option test to `TestListTradePnlByStrategy`**

Add this test at the end of the `TestListTradePnlByStrategy` class (after `test_negative_pnl_for_losing_trade`):

```python
    def test_option_trade_pnl_applies_100x_multiplier(self) -> None:
        """Option trade: (0.80 - 1.20) × 2 × 100 = -80.0."""
        # row: (strategy_name, exit_date, qty, exit_fill, entry_fill)
        rows = [("option", date(2026, 1, 2), 2, 0.80, 1.20)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 1
        assert result[0]["strategy_name"] == "option"
        assert result[0]["pnl"] == pytest.approx(-80.0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/unit/test_storage_db.py::TestListTradePnlByStrategy::test_option_trade_pnl_applies_100x_multiplier -v
```

Expected: FAIL — current code returns `pnl = -0.8` instead of `-80.0`.

- [ ] **Step 3: Fix the dict comprehension in `list_trade_pnl_by_strategy`**

In `src/alpaca_bot/storage/repositories.py`, inside `list_trade_pnl_by_strategy`, find the `return [...]` list comprehension. It currently reads:

```python
        return [
            {
                "strategy_name": row[0],
                "exit_date": row[1],
                "pnl": (float(row[3]) - float(row[4])) * float(row[2]),
            }
            for row in rows
            if row[4] is not None
        ]
```

Replace with:

```python
        return [
            {
                "strategy_name": row[0],
                "exit_date": row[1],
                "pnl": (float(row[3]) - float(row[4])) * float(row[2]) * _contract_multiplier(row[0]),
            }
            for row in rows
            if row[4] is not None
        ]
```

- [ ] **Step 4: Run all `TestListTradePnlByStrategy` tests to verify they all pass**

```bash
pytest tests/unit/test_storage_db.py::TestListTradePnlByStrategy -v
```

Expected: 6 tests PASS (5 existing + 1 new).

- [ ] **Step 5: Run the full test suite**

```bash
pytest
```

Expected: All tests PASS. If any test fails, read the error carefully — the most likely cause is a row-index mismatch in `daily_realized_pnl_by_symbol` (check that the `missing_entry` log block was moved before the aggregation loop, not left after it).

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/storage/repositories.py tests/unit/test_storage_db.py
git commit -m "fix: apply x100 multiplier to option trades in list_trade_pnl_by_strategy"
```

---

## Done

All three PnL functions now apply `_contract_multiplier`. The supervisor's portfolio loss limit, per-symbol loss limit, and losing-streak detection all compute correct dollar amounts for option trades.

**What was deliberately left out:**
- `lifetime_pnl_by_strategy` — pure SQL CTE with GROUP BY; fix requires a `CASE WHEN strategy_name = 'option' THEN 100 ELSE 1 END` multiplier *inside* the CTE before aggregation. Reporting-only, not a safety gate. Deferred to a separate spec.
- `win_loss_counts_by_strategy` — sign comparison only (positive = win, negative = loss). Multiplying by 100 does not change the sign. No fix needed.

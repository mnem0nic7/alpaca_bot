# Short Option ×100 Multiplier Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the ×100 contract multiplier to `"short_option"` positions in all 5 locations that currently only apply it to `"option"`.

**Architecture:** Two Python helpers (`_option_multiplier` in `web/service.py`, `_contract_multiplier` in `storage/repositories.py`) already centralize the multiplier check for their callers — updating them fixes those callers (capital pct, total notional, daily realized PnL, per-symbol PnL, losing-streak detection) automatically. Two remaining inline checks (`_to_trade_record` and `lifetime_pnl_by_strategy` SQL) are updated directly. The Jinja2 template gets a matching tuple check.

**Tech Stack:** Python, Jinja2, SQL (psycopg2-style), pytest.

---

## Files

- **Modify:** `src/alpaca_bot/web/service.py` — lines 189 and 512
- **Modify:** `src/alpaca_bot/storage/repositories.py` — lines 247 and 871
- **Modify:** `src/alpaca_bot/web/templates/dashboard.html` — line 451
- **Test:** `tests/unit/test_web_service.py` — add 3 tests after line 1819
- **Test:** `tests/unit/test_storage_db.py` — add 3 tests: 1 method inside `TestListTradePnlByStrategy`, 1 method inside `TestDailyRealizedPnl`, 1 standalone function

---

### Task 1: Fix `web/service.py` — Python helpers + trade record

**Files:**
- Modify: `src/alpaca_bot/web/service.py:189,512`
- Test: `tests/unit/test_web_service.py`

- [ ] **Step 1: Write 3 failing tests**

Add these three tests at the very end of `tests/unit/test_web_service.py` (after the last test on line 1820):

```python
def test_to_trade_record_short_option_pnl_multiplied_by_100() -> None:
    row = {
        "symbol": "BCRX260618P00009000",
        "strategy_name": "short_option",
        "entry_fill": 0.55,
        "entry_limit": None,
        "entry_time": None,
        "exit_time": None,
        "exit_fill": 0.70,
        "qty": 7,
        "intent_type": "stop",
    }
    trade = _to_trade_record(row)
    # (0.70 - 0.55) * 7 * 100 = 105.0
    assert abs(trade.pnl - 105.0) < 1e-9


def test_compute_capital_pct_short_option_multiplies_by_100() -> None:
    # _compute_capital_pct guards against total <= 0, so use positive qty to reach the assertion.
    short_opt_pos = SimpleNamespace(
        symbol="BCRX260618P00009000",
        quantity=7,
        entry_price=0.55,
        strategy_name="short_option",
    )
    equity_pos = SimpleNamespace(
        symbol="AAPL",
        quantity=10,
        entry_price=150.0,
        strategy_name="breakout",
    )
    # short_option notional = 0.55 * 7 * 100 = 385
    # equity notional = 150.0 * 10 * 1 = 1500
    # total = 1885; short_option pct = 385/1885*100 ≈ 20.42
    result = _compute_capital_pct([short_opt_pos, equity_pos], {})
    short_opt_pct = result.get("short_option", 0.0)
    assert abs(short_opt_pct - 385.0 / 1885.0 * 100) < 0.1


def test_total_deployed_notional_short_option_multiplied() -> None:
    equity_pos = SimpleNamespace(
        symbol="AAPL", quantity=5, entry_price=150.0, strategy_name="breakout"
    )
    short_opt_pos = SimpleNamespace(
        symbol="BCRX260618P00009000", quantity=-7, entry_price=0.55, strategy_name="short_option"
    )
    result = total_deployed_notional([equity_pos, short_opt_pos])
    # 5 * 150.0 * 1 + (-7) * 0.55 * 100 = 750.0 + (-385.0) = 365.0
    assert abs(result - 365.0) < 1e-9
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py::test_to_trade_record_short_option_pnl_multiplied_by_100 tests/unit/test_web_service.py::test_compute_capital_pct_short_option_multiplies_by_100 tests/unit/test_web_service.py::test_total_deployed_notional_short_option_multiplied -v
```

Expected: 3 FAILures. `test_to_trade_record` will fail because `pnl = 1.05` (no multiplier) not `105.0`. The capital pct and notional tests will fail because `_option_multiplier` returns 1 for `short_option`.

- [ ] **Step 3: Fix `_option_multiplier` at line 189**

Change:
```python
def _option_multiplier(pos) -> int:
    return 100 if getattr(pos, "strategy_name", None) == "option" else 1
```

To:
```python
def _option_multiplier(pos) -> int:
    return 100 if getattr(pos, "strategy_name", None) in ("option", "short_option") else 1
```

- [ ] **Step 4: Fix `_to_trade_record` inline check at line 512**

Change:
```python
    multiplier = 100 if row.get("strategy_name") == "option" else 1
```

To:
```python
    multiplier = 100 if row.get("strategy_name") in ("option", "short_option") else 1
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/test_web_service.py::test_to_trade_record_short_option_pnl_multiplied_by_100 tests/unit/test_web_service.py::test_compute_capital_pct_short_option_multiplies_by_100 tests/unit/test_web_service.py::test_total_deployed_notional_short_option_multiplied -v
```

Expected: 3 PASSes.

- [ ] **Step 6: Run full web service test suite to check for regressions**

```bash
pytest tests/unit/test_web_service.py -v
```

Expected: all tests PASS (no regressions in existing `"option"` tests).

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_web_service.py src/alpaca_bot/web/service.py
git commit -m "fix: apply ×100 multiplier to short_option in web/service.py"
```

---

### Task 2: Fix `storage/repositories.py` — helper + SQL

**Files:**
- Modify: `src/alpaca_bot/storage/repositories.py:247,871`
- Test: `tests/unit/test_storage_db.py`

- [ ] **Step 1: Write 3 failing tests**

**Test A** — Add inside `TestListTradePnlByStrategy` (after line 700, inside the class body — match the 4-space indentation of the other methods):

```python
    def test_short_option_trade_pnl_applies_100x_multiplier(self) -> None:
        """short_option trade: (0.70 - 0.55) × 7 × 100 = 105.0."""
        # row: (strategy_name, exit_date, qty, exit_fill, entry_fill)
        rows = [("short_option", date(2026, 1, 2), 7, 0.70, 0.55)]
        store = self._make_store(rows)
        result = store.list_trade_pnl_by_strategy(
            trading_mode=TradingMode.PAPER,
            strategy_version="v1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 28),
        )
        assert len(result) == 1
        assert result[0]["strategy_name"] == "short_option"
        assert result[0]["pnl"] == pytest.approx(105.0)
```

**Test B** — Add inside `TestDailyRealizedPnl` (after `test_option_null_entry_fill_fail_safe_also_multiplied` at line ~349, inside the class body):

```python
    def test_short_option_trade_applies_100x_multiplier(self):
        """short_option exit applies ×100: (0.70 - 0.55) × 7 × 100 = 105.0.
        This directly tests the safety-gate path — daily_realized_pnl feeds the
        portfolio loss-limit check and must apply the multiplier for short_option."""
        rows = [("BCRX260618P00009000", 0.55, 0.70, 7, "short_option")]
        store = self._store(rows)
        pnl = store.daily_realized_pnl(
            trading_mode=self.MODE,
            strategy_version=self.STRATEGY,
            session_date=self.SESSION_DATE,
        )
        assert pnl == pytest.approx(105.0)
```

**Test C** — Add as a standalone module-level function after the `TestListTradePnlByStrategy` class (not inside any class — no indentation):

```python
def test_contract_multiplier_short_option_returns_100() -> None:
    """_contract_multiplier must return 100 for 'short_option', just like 'option'."""
    from alpaca_bot.storage.repositories import _contract_multiplier
    assert _contract_multiplier("short_option") == 100
    assert _contract_multiplier("option") == 100
    assert _contract_multiplier("breakout") == 1
    assert _contract_multiplier(None) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest "tests/unit/test_storage_db.py::TestListTradePnlByStrategy::test_short_option_trade_pnl_applies_100x_multiplier" "tests/unit/test_storage_db.py::TestDailyRealizedPnl::test_short_option_trade_applies_100x_multiplier" tests/unit/test_storage_db.py::test_contract_multiplier_short_option_returns_100 -v
```

Expected: 3 FAILures. All three fail because `_contract_multiplier("short_option")` returns 1 instead of 100.

- [ ] **Step 3: Fix `_contract_multiplier` at line 247**

Change:
```python
def _contract_multiplier(strategy_name: str | None) -> int:
    return 100 if strategy_name == "option" else 1
```

To:
```python
def _contract_multiplier(strategy_name: str | None) -> int:
    return 100 if strategy_name in ("option", "short_option") else 1
```

- [ ] **Step 4: Fix the SQL CASE at line 871**

Change:
```sql
                           * CASE WHEN x.strategy_name = 'option' THEN 100 ELSE 1 END AS pnl
```

To:
```sql
                           * CASE WHEN x.strategy_name IN ('option', 'short_option') THEN 100 ELSE 1 END AS pnl
```

(This is inside the `lifetime_pnl_by_strategy` method's SQL string in `repositories.py`.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest "tests/unit/test_storage_db.py::TestListTradePnlByStrategy::test_short_option_trade_pnl_applies_100x_multiplier" tests/unit/test_storage_db.py::test_contract_multiplier_short_option_returns_100 -v
```

Expected: 2 PASSes.

- [ ] **Step 6: Run full storage test suite to check for regressions**

```bash
pytest tests/unit/test_storage_db.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_storage_db.py src/alpaca_bot/storage/repositories.py
git commit -m "fix: apply ×100 multiplier to short_option in repositories.py"
```

---

### Task 3: Fix `dashboard.html` template + full suite

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html:451`

The Jinja2 template `in` operator accepts tuples just like Python. No dedicated Python unit test exists for Jinja2 rendering, so we verify by reading the changed line and running the full suite.

- [ ] **Step 1: Fix the template multiplier at line 451**

Change:
```jinja
                  {% set multiplier = 100 if position.strategy_name == "option" else 1 %}
```

To:
```jinja
                  {% set multiplier = 100 if position.strategy_name in ("option", "short_option") else 1 %}
```

- [ ] **Step 2: Run full test suite**

```bash
pytest
```

Expected: all tests PASS. The dashboard HTML is exercised by `tests/unit/test_web_app.py` template-rendering tests which will pick up the change.

- [ ] **Step 3: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "fix: apply ×100 multiplier to short_option in dashboard template"
```

# Per-Trade Dollar Loss Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `MAX_LOSS_PER_TRADE_DOLLARS` — an optional hard dollar cap on per-trade quantity so that a clean stop-out never loses more than the configured dollar amount, regardless of stop tightness.

**Architecture:** Two files change. `config/__init__.py` gains a new optional field parsed from the env var. `risk/sizing.py` applies it as an additional quantity constraint after the risk-budget calculation and before the notional cap. No DB schema changes, no audit events, no order-dispatch changes.

**Tech Stack:** Python, pytest, SimpleNamespace for test settings fakes.

---

### Task 1: Write failing tests for the dollar loss cap

**Files:**
- Modify: `tests/unit/test_position_sizing.py`

The existing `make_settings` helper passes a `SimpleNamespace` to `calculate_position_size`. Add `max_loss_per_trade_dollars` to the helper so new tests can exercise the new code path.

- [ ] **Step 1: Add `max_loss_per_trade_dollars` parameter to `make_settings`**

In `tests/unit/test_position_sizing.py`, replace the existing `make_settings` function:

```python
def make_settings(
    *,
    risk_per_trade_pct: float = 0.0025,
    max_position_pct: float = 0.05,
    max_loss_per_trade_dollars: float | None = None,
) -> object:
    return SimpleNamespace(
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=max_position_pct,
        max_loss_per_trade_dollars=max_loss_per_trade_dollars,
    )
```

- [ ] **Step 2: Add three new test cases at the bottom of the file**

```python
class TestDollarLossCap:
    def test_dollar_cap_is_binding_when_stop_is_tight(self):
        """When dollar cap < risk budget, quantity is reduced to honour the cap."""
        # equity=10_000, risk=0.25% → budget=$25; entry=100, stop=99 → risk/share=$1 → qty=25
        # dollar_cap=10 → dollar_cap_qty=10/1=10 → 10 < 25 → cap wins → qty=10
        settings = make_settings(
            risk_per_trade_pct=0.0025,
            max_position_pct=0.10,
            max_loss_per_trade_dollars=10.0,
        )
        qty = calculate_position_size(
            equity=10_000.0,
            entry_price=100.0,
            stop_price=99.0,
            settings=settings,
        )
        assert qty == 10

    def test_dollar_cap_is_not_binding_when_stop_is_wide(self):
        """When dollar cap > risk budget, the risk budget is the binding constraint."""
        # equity=10_000, risk=0.25% → budget=$25; entry=100, stop=95 → risk/share=$5 → qty=5
        # dollar_cap=50 → dollar_cap_qty=50/5=10 → 10 > 5 → risk budget wins → qty=5
        settings = make_settings(
            risk_per_trade_pct=0.0025,
            max_position_pct=0.10,
            max_loss_per_trade_dollars=50.0,
        )
        qty = calculate_position_size(
            equity=10_000.0,
            entry_price=100.0,
            stop_price=95.0,
            settings=settings,
        )
        assert qty == 5

    def test_dollar_cap_none_preserves_existing_behaviour(self):
        """When max_loss_per_trade_dollars is None, behaviour is unchanged."""
        settings = make_settings(
            risk_per_trade_pct=0.0025,
            max_position_pct=0.10,
            max_loss_per_trade_dollars=None,
        )
        qty = calculate_position_size(
            equity=10_000.0,
            entry_price=100.0,
            stop_price=99.0,
            settings=settings,
        )
        # risk_budget=$25, risk/share=$1 → qty=25 (no cap applied)
        assert qty == 25
```

- [ ] **Step 3: Run the tests to confirm they fail**

```bash
pytest tests/unit/test_position_sizing.py::TestDollarLossCap -v
```

Expected: FAIL — `SimpleNamespace` in `make_settings` for existing tests doesn't have `max_loss_per_trade_dollars`, and `calculate_position_size` doesn't yet read it.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/unit/test_position_sizing.py
git commit -m "test: failing tests for MAX_LOSS_PER_TRADE_DOLLARS dollar cap in sizing"
```

---

### Task 2: Add `max_loss_per_trade_dollars` to `Settings`

**Files:**
- Modify: `src/alpaca_bot/config/__init__.py`

- [ ] **Step 1: Add the field to the `Settings` dataclass**

After `intraday_consecutive_loss_gate: int = 0` (last field before `__post_init__`), add:

```python
max_loss_per_trade_dollars: float | None = None
```

- [ ] **Step 2: Parse it in `from_env()`**

After the `intraday_consecutive_loss_gate=...` line in the `cls(...)` call, add:

```python
max_loss_per_trade_dollars=(
    float(values["MAX_LOSS_PER_TRADE_DOLLARS"])
    if "MAX_LOSS_PER_TRADE_DOLLARS" in values
    else None
),
```

- [ ] **Step 3: Validate it in `validate()`**

After the `intraday_consecutive_loss_gate` validation block, add:

```python
if self.max_loss_per_trade_dollars is not None and self.max_loss_per_trade_dollars <= 0:
    raise ValueError("MAX_LOSS_PER_TRADE_DOLLARS must be > 0")
```

- [ ] **Step 4: Run settings-related tests to make sure nothing broke**

```bash
pytest tests/unit/test_settings_stop_cap.py tests/unit/test_settings_trailing_stop.py tests/unit/test_settings_profit_trail.py -v
```

Expected: all pass (new field has a default, so existing tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/config/__init__.py
git commit -m "feat: add MAX_LOSS_PER_TRADE_DOLLARS to Settings"
```

---

### Task 3: Implement the dollar cap in `calculate_position_size()`

**Files:**
- Modify: `src/alpaca_bot/risk/sizing.py`

- [ ] **Step 1: Add the dollar cap logic**

In `calculate_position_size()`, after the line `if quantity <= 0.0: return 0.0` (line 29) and **before** the `max_notional` cap block, insert:

```python
if settings.max_loss_per_trade_dollars is not None:
    dollar_cap_qty = settings.max_loss_per_trade_dollars / risk_per_share
    quantity = min(quantity, dollar_cap_qty)
    if not fractionable:
        quantity = math.floor(quantity)
    if not fractionable and quantity < 1:
        return 0.0
    if quantity <= 0.0:
        return 0.0
```

Direct attribute access is correct: every caller either uses real `Settings` (which will have the field after Task 2) or the updated `SimpleNamespace` from `make_settings()` in `test_position_sizing.py` (updated in Task 1).

- [ ] **Step 2: Run the new dollar cap tests**

```bash
pytest tests/unit/test_position_sizing.py::TestDollarLossCap -v
```

Expected: all 3 tests PASS.

- [ ] **Step 3: Run the full sizing test file**

```bash
pytest tests/unit/test_position_sizing.py -v
```

Expected: all tests PASS (existing tests use `SimpleNamespace` without `max_loss_per_trade_dollars` — the `getattr` default handles this safely).

- [ ] **Step 4: Run the full test suite**

```bash
pytest
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/risk/sizing.py
git commit -m "feat: cap position size by MAX_LOSS_PER_TRADE_DOLLARS in calculate_position_size"
```

---

### Task 4: Add a settings test for `MAX_LOSS_PER_TRADE_DOLLARS`

**Files:**
- Modify: `tests/unit/test_settings_stop_cap.py` (or a new file if that file is only about stop cap)

Check what `test_settings_stop_cap.py` contains and add these tests there (or in a new `test_settings_dollar_cap.py` if the file would be unrelated).

- [ ] **Step 1: Write the settings tests**

```python
from alpaca_bot.config import Settings
import pytest

def _base_env(**overrides):
    base = {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1",
        "DATABASE_URL": "postgresql://x:y@localhost/db",
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
        "PER_SYMBOL_LOSS_LIMIT_PCT": "0.0",
    }
    base.update(overrides)
    return base


def test_max_loss_per_trade_dollars_defaults_to_none():
    s = Settings.from_env(_base_env())
    assert s.max_loss_per_trade_dollars is None


def test_max_loss_per_trade_dollars_parsed_from_env():
    s = Settings.from_env(_base_env(MAX_LOSS_PER_TRADE_DOLLARS="15.0"))
    assert s.max_loss_per_trade_dollars == 15.0


def test_max_loss_per_trade_dollars_zero_raises():
    with pytest.raises(ValueError, match="MAX_LOSS_PER_TRADE_DOLLARS must be > 0"):
        Settings.from_env(_base_env(MAX_LOSS_PER_TRADE_DOLLARS="0"))


def test_max_loss_per_trade_dollars_negative_raises():
    with pytest.raises(ValueError, match="MAX_LOSS_PER_TRADE_DOLLARS must be > 0"):
        Settings.from_env(_base_env(MAX_LOSS_PER_TRADE_DOLLARS="-5"))
```

- [ ] **Step 2: Run the settings tests**

```bash
pytest tests/unit/test_settings_dollar_cap.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 3: Run the full test suite one final time**

```bash
pytest
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_settings_dollar_cap.py
git commit -m "test: Settings validation for MAX_LOSS_PER_TRADE_DOLLARS"
```

---

### Task 5: Document `MAX_LOSS_PER_TRADE_DOLLARS` in DEPLOYMENT.md

**Files:**
- Modify: `DEPLOYMENT.md`

- [ ] **Step 1: Add the env var near `RISK_PER_TRADE_PCT`**

In `DEPLOYMENT.md`, after the `RISK_PER_TRADE_PCT=0.0025` line (around line 46), add:

```
# Per-trade dollar loss cap: limit how much a single stopped-out trade can lose in absolute
# dollar terms. When set, position size is reduced so that a clean stop-out loses at most
# this amount. Composable with RISK_PER_TRADE_PCT — the tighter constraint wins.
# (unset = disabled; recommended starting value for a ~$10K account: 12)
# MAX_LOSS_PER_TRADE_DOLLARS=12
```

- [ ] **Step 2: Commit**

```bash
git add DEPLOYMENT.md
git commit -m "docs: document MAX_LOSS_PER_TRADE_DOLLARS in DEPLOYMENT.md env template"
```

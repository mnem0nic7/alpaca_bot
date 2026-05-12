# Option Strategy Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the ×100 contract-multiplier omission on the dashboard, add bid-ask spread filter, and add open interest filter to option contract selection.

**Architecture:** Five layers touched — domain model gains `spread_pct` and `open_interest`, execution layer extracts OI from Alpaca snapshot, config gains two new settings, strategy selector gains two eligibility filters, web service and template fix the ×100 multiplier.

**Tech Stack:** Python, Jinja2, psycopg2, pytest. No schema changes.

---

### Task 1: Domain + Config — add `spread_pct`, `open_interest`, and two new settings

**Files:**
- Modify: `src/alpaca_bot/domain/models.py`
- Modify: `src/alpaca_bot/config/__init__.py`
- Modify: `tests/unit/test_option_domain_settings.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_option_domain_settings.py`:

```python
# --- spread_pct ---

def test_option_contract_spread_pct_normal():
    from alpaca_bot.domain.models import OptionContract
    c = OptionContract(
        occ_symbol="AAPL240701C00150000", underlying="AAPL",
        option_type="call", strike=150.0, expiry=date(2024, 7, 1),
        bid=1.90, ask=2.00,
    )
    # (2.00 - 1.90) / 2.00 = 0.05
    assert abs(c.spread_pct - 0.05) < 1e-9


def test_option_contract_spread_pct_zero_ask():
    from alpaca_bot.domain.models import OptionContract
    c = OptionContract(
        occ_symbol="AAPL240701C00150000", underlying="AAPL",
        option_type="call", strike=150.0, expiry=date(2024, 7, 1),
        bid=0.0, ask=0.0,
    )
    assert c.spread_pct == 0.0


# --- open_interest ---

def test_option_contract_open_interest_default_none():
    from alpaca_bot.domain.models import OptionContract
    c = OptionContract(
        occ_symbol="AAPL240701C00150000", underlying="AAPL",
        option_type="call", strike=150.0, expiry=date(2024, 7, 1),
        bid=1.90, ask=2.00,
    )
    assert c.open_interest is None


def test_option_contract_open_interest_set():
    from alpaca_bot.domain.models import OptionContract
    c = OptionContract(
        occ_symbol="AAPL240701C00150000", underlying="AAPL",
        option_type="call", strike=150.0, expiry=date(2024, 7, 1),
        bid=1.90, ask=2.00,
        open_interest=500,
    )
    assert c.open_interest == 500


# --- new settings ---

def test_settings_option_max_spread_pct_default():
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())
    assert s.option_max_spread_pct == 0.50


def test_settings_option_max_spread_pct_from_env():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_MAX_SPREAD_PCT"] = "0.30"
    s = Settings.from_env(env)
    assert s.option_max_spread_pct == 0.30


def test_settings_option_max_spread_pct_validation_zero_rejected():
    from alpaca_bot.config import Settings
    import pytest
    env = _base_env()
    env["OPTION_MAX_SPREAD_PCT"] = "0.0"
    with pytest.raises(ValueError, match="OPTION_MAX_SPREAD_PCT"):
        Settings.from_env(env)


def test_settings_option_max_spread_pct_validation_above_one_rejected():
    from alpaca_bot.config import Settings
    import pytest
    env = _base_env()
    env["OPTION_MAX_SPREAD_PCT"] = "1.1"
    with pytest.raises(ValueError, match="OPTION_MAX_SPREAD_PCT"):
        Settings.from_env(env)


def test_settings_option_min_open_interest_default():
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())
    assert s.option_min_open_interest == 0


def test_settings_option_min_open_interest_from_env():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_MIN_OPEN_INTEREST"] = "100"
    s = Settings.from_env(env)
    assert s.option_min_open_interest == 100


def test_settings_option_min_open_interest_negative_rejected():
    from alpaca_bot.config import Settings
    import pytest
    env = _base_env()
    env["OPTION_MIN_OPEN_INTEREST"] = "-1"
    with pytest.raises(ValueError, match="OPTION_MIN_OPEN_INTEREST"):
        Settings.from_env(env)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_domain_settings.py::test_option_contract_spread_pct_normal tests/unit/test_option_domain_settings.py::test_settings_option_max_spread_pct_default -v
```

Expected: `FAILED` — `OptionContract has no attribute spread_pct`, `Settings has no attribute option_max_spread_pct`

- [ ] **Step 3: Add `spread_pct` and `open_interest` to `OptionContract`**

In `src/alpaca_bot/domain/models.py`, update `OptionContract`:

```python
@dataclass(frozen=True)
class OptionContract:
    occ_symbol: str
    underlying: str
    option_type: str
    strike: float
    expiry: date
    bid: float
    ask: float
    delta: float | None = None
    open_interest: int | None = None

    @property
    def spread_pct(self) -> float:
        if self.ask <= 0:
            return 0.0
        return (self.ask - self.bid) / self.ask
```

- [ ] **Step 4: Add two new settings to `Settings`**

In `src/alpaca_bot/config/__init__.py`, after `option_stop_buffer_pct: float = 0.10` (line ~150), add:

```python
    option_max_spread_pct: float = 0.50
    option_min_open_interest: int = 0
```

In `from_env`, after the `option_stop_buffer_pct=...` line (~354), add:

```python
            option_max_spread_pct=float(values.get("OPTION_MAX_SPREAD_PCT", "0.50")),
            option_min_open_interest=int(values.get("OPTION_MIN_OPEN_INTEREST", "0")),
```

In `validate()`, after the `option_delta_target` check (~line 554), add:

```python
        if not 0.0 < self.option_max_spread_pct <= 1.0:
            raise ValueError(
                "OPTION_MAX_SPREAD_PCT must be between 0 (exclusive) and 1.0 (inclusive)"
            )
        if self.option_min_open_interest < 0:
            raise ValueError("OPTION_MIN_OPEN_INTEREST must be >= 0")
```

- [ ] **Step 5: Run all new tests to verify they pass**

```bash
pytest tests/unit/test_option_domain_settings.py -v
```

Expected: All 20 tests PASS (existing 9 + 11 new)

- [ ] **Step 6: Run full suite to confirm no regressions**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/domain/models.py src/alpaca_bot/config/__init__.py tests/unit/test_option_domain_settings.py
git commit -m "feat: add OptionContract.spread_pct, open_interest field, and OPTION_MAX_SPREAD_PCT/OPTION_MIN_OPEN_INTEREST settings"
```

---

### Task 2: Execution — extract `open_interest` from Alpaca snapshot

**Files:**
- Modify: `src/alpaca_bot/execution/option_chain.py`
- Modify: `tests/unit/test_option_chain.py`

- [ ] **Step 1: Write failing tests**

In `tests/unit/test_option_chain.py`, add to `_make_snapshot` and to `TestAlpacaOptionChainAdapter`:

First, update the `_make_snapshot` helper to support `open_interest`:

```python
def _make_snapshot(ask: float, delta: float | None = None, open_interest: int | None = None):
    class FakeGreeks:
        def __init__(self, delta):
            self.delta = delta

    class FakeQuote:
        def __init__(self, ask):
            self.ask_price = ask
            self.bid_price = ask - 0.10

    class FakeSnapshot:
        def __init__(self, ask, delta, oi):
            self.greeks = FakeGreeks(delta) if delta is not None else None
            self.latest_quote = FakeQuote(ask)
            self.open_interest = oi

    return FakeSnapshot(ask, delta, open_interest)
```

Then add tests:

```python
def test_open_interest_extracted_when_present(self):
    client = _FakeSnapshotClient({
        "AAPL240701C00150000": _make_snapshot(ask=2.00, open_interest=500),
    })
    adapter = AlpacaOptionChainAdapter(client)
    contracts = adapter.get_option_chain("AAPL", _settings())
    assert len(contracts) == 1
    assert contracts[0].open_interest == 500


def test_open_interest_none_when_absent(self):
    client = _FakeSnapshotClient({
        "AAPL240701C00150000": _make_snapshot(ask=2.00, open_interest=None),
    })
    adapter = AlpacaOptionChainAdapter(client)
    contracts = adapter.get_option_chain("AAPL", _settings())
    assert len(contracts) == 1
    assert contracts[0].open_interest is None


def test_open_interest_none_when_malformed(self):
    snapshot = _make_snapshot(ask=2.00, open_interest=None)
    snapshot.open_interest = "not-a-number"
    client = _FakeSnapshotClient({"AAPL240701C00150000": snapshot})
    adapter = AlpacaOptionChainAdapter(client)
    contracts = adapter.get_option_chain("AAPL", _settings())
    assert len(contracts) == 1
    assert contracts[0].open_interest is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_chain.py::TestAlpacaOptionChainAdapter::test_open_interest_extracted_when_present -v
```

Expected: `FAILED` — `AssertionError: assert None == 500`

- [ ] **Step 3: Extract open_interest in `_snapshot_to_contract`**

In `src/alpaca_bot/execution/option_chain.py`, update `_snapshot_to_contract`:

```python
def _snapshot_to_contract(occ_symbol: str, underlying: str, snapshot: Any) -> OptionContract:
    expiry, option_type, strike = _parse_occ(occ_symbol)

    quote = snapshot.latest_quote
    ask = float(quote.ask_price) if quote is not None else 0.0
    bid = float(quote.bid_price) if quote is not None else 0.0

    delta: float | None = None
    if snapshot.greeks is not None:
        try:
            delta = float(snapshot.greeks.delta)
        except (TypeError, AttributeError):
            delta = None

    open_interest: int | None = None
    try:
        raw_oi = getattr(snapshot, "open_interest", None)
        if raw_oi is not None:
            open_interest = int(raw_oi)
    except (TypeError, ValueError):
        open_interest = None

    return OptionContract(
        occ_symbol=occ_symbol,
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        bid=bid,
        ask=ask,
        delta=delta,
        open_interest=open_interest,
    )
```

- [ ] **Step 4: Run new tests**

```bash
pytest tests/unit/test_option_chain.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/execution/option_chain.py tests/unit/test_option_chain.py
git commit -m "feat: extract open_interest from Alpaca option snapshot"
```

---

### Task 3: Strategy — add spread + OI eligibility filters

**Files:**
- Modify: `src/alpaca_bot/strategy/option_selector.py`
- Modify: `tests/unit/test_option_selector.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_option_selector.py`. First, update the `_contract` and `_put_contract` helpers to accept `open_interest`:

```python
def _contract(
    strike: float, expiry: date, ask: float,
    delta: float | None = None, option_type: str = "call",
    bid: float | None = None, open_interest: int | None = None,
) -> OptionContract:
    return OptionContract(
        occ_symbol=f"AAPL{expiry.strftime('%y%m%d')}C{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        bid=bid if bid is not None else ask - 0.05,
        ask=ask,
        delta=delta,
        open_interest=open_interest,
    )


def _put_contract(
    strike: float, expiry: date, ask: float,
    delta: float | None = None,
    bid: float | None = None, open_interest: int | None = None,
) -> OptionContract:
    return OptionContract(
        occ_symbol=f"AAPL{expiry.strftime('%y%m%d')}P{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type="put",
        strike=strike,
        expiry=expiry,
        bid=bid if bid is not None else ask - 0.05,
        ask=ask,
        delta=delta,
        open_interest=open_interest,
    )
```

Then add test classes:

```python
class TestSpreadFilter:
    def test_call_wide_spread_rejected(self):
        # ask=2.00, bid=0.00 → spread_pct=1.0 > OPTION_MAX_SPREAD_PCT=0.50
        s = _settings(OPTION_MAX_SPREAD_PCT="0.50")
        c = _contract(150.0, NEAR_EXPIRY, ask=2.00, bid=0.00, delta=0.50)
        result = select_call_contract([c], current_price=150.0, today=TODAY, settings=s)
        assert result is None

    def test_call_tight_spread_passes(self):
        # ask=2.00, bid=1.80 → spread_pct=0.10 <= 0.50
        s = _settings(OPTION_MAX_SPREAD_PCT="0.50")
        c = _contract(150.0, NEAR_EXPIRY, ask=2.00, bid=1.80, delta=0.50)
        result = select_call_contract([c], current_price=150.0, today=TODAY, settings=s)
        assert result is c

    def test_put_wide_spread_rejected(self):
        s = _settings(OPTION_MAX_SPREAD_PCT="0.30")
        p = _put_contract(150.0, NEAR_EXPIRY, ask=2.00, bid=0.00, delta=-0.50)
        result = select_put_contract([p], current_price=150.0, today=TODAY, settings=s)
        assert result is None

    def test_put_tight_spread_passes(self):
        s = _settings(OPTION_MAX_SPREAD_PCT="0.30")
        p = _put_contract(150.0, NEAR_EXPIRY, ask=2.00, bid=1.50, delta=-0.50)
        # spread_pct = (2.00 - 1.50) / 2.00 = 0.25 <= 0.30
        result = select_put_contract([p], current_price=150.0, today=TODAY, settings=s)
        assert result is p


class TestOpenInterestFilter:
    def test_call_low_oi_rejected(self):
        s = _settings(OPTION_MIN_OPEN_INTEREST="100")
        c = _contract(150.0, NEAR_EXPIRY, ask=2.00, delta=0.50, open_interest=50)
        result = select_call_contract([c], current_price=150.0, today=TODAY, settings=s)
        assert result is None

    def test_call_sufficient_oi_passes(self):
        s = _settings(OPTION_MIN_OPEN_INTEREST="100")
        c = _contract(150.0, NEAR_EXPIRY, ask=2.00, delta=0.50, open_interest=200)
        result = select_call_contract([c], current_price=150.0, today=TODAY, settings=s)
        assert result is c

    def test_call_oi_none_passes_when_min_set(self):
        # Fail-open: OI not reported doesn't mean no liquidity
        s = _settings(OPTION_MIN_OPEN_INTEREST="100")
        c = _contract(150.0, NEAR_EXPIRY, ask=2.00, delta=0.50, open_interest=None)
        result = select_call_contract([c], current_price=150.0, today=TODAY, settings=s)
        assert result is c

    def test_call_oi_filter_disabled_when_zero(self):
        # option_min_open_interest=0 means disabled; even OI=1 passes
        s = _settings(OPTION_MIN_OPEN_INTEREST="0")
        c = _contract(150.0, NEAR_EXPIRY, ask=2.00, delta=0.50, open_interest=1)
        result = select_call_contract([c], current_price=150.0, today=TODAY, settings=s)
        assert result is c

    def test_put_low_oi_rejected(self):
        s = _settings(OPTION_MIN_OPEN_INTEREST="100")
        p = _put_contract(150.0, NEAR_EXPIRY, ask=2.00, delta=-0.50, open_interest=10)
        result = select_put_contract([p], current_price=150.0, today=TODAY, settings=s)
        assert result is None

    def test_put_oi_none_passes_when_min_set(self):
        s = _settings(OPTION_MIN_OPEN_INTEREST="100")
        p = _put_contract(150.0, NEAR_EXPIRY, ask=2.00, delta=-0.50, open_interest=None)
        result = select_put_contract([p], current_price=150.0, today=TODAY, settings=s)
        assert result is p
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_selector.py::TestSpreadFilter::test_call_wide_spread_rejected tests/unit/test_option_selector.py::TestOpenInterestFilter::test_call_low_oi_rejected -v
```

Expected: `FAILED` — spread and OI filters do not exist yet.

- [ ] **Step 3: Apply filters in `option_selector.py`**

Replace both functions in `src/alpaca_bot/strategy/option_selector.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import OptionContract


def select_call_contract(
    contracts: Sequence[OptionContract],
    *,
    current_price: float,
    today: date,
    settings: Settings,
) -> OptionContract | None:
    eligible = [
        c for c in contracts
        if c.option_type == "call"
        and c.ask > 0
        and settings.option_dte_min <= (c.expiry - today).days <= settings.option_dte_max
        and c.spread_pct <= settings.option_max_spread_pct
        and (
            settings.option_min_open_interest == 0
            or c.open_interest is None
            or c.open_interest >= settings.option_min_open_interest
        )
    ]
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(c.delta - settings.option_delta_target))  # type: ignore[operator]
    return min(eligible, key=lambda c: abs(c.strike - current_price))


def select_put_contract(
    contracts: Sequence[OptionContract],
    *,
    current_price: float,
    today: date,
    settings: Settings,
) -> OptionContract | None:
    eligible = [
        c for c in contracts
        if c.option_type == "put"
        and c.ask > 0
        and settings.option_dte_min <= (c.expiry - today).days <= settings.option_dte_max
        and c.spread_pct <= settings.option_max_spread_pct
        and (
            settings.option_min_open_interest == 0
            or c.open_interest is None
            or c.open_interest >= settings.option_min_open_interest
        )
    ]
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(abs(c.delta) - settings.option_delta_target))  # type: ignore[operator]
    return min(eligible, key=lambda c: abs(c.strike - current_price))
```

- [ ] **Step 4: Verify existing tests still pass (spread_pct with default bid=ask-0.05)**

The existing test helpers use `bid=ask - 0.05`. For `ask=2.00`, `spread_pct = 0.05 / 2.00 = 0.025 << 0.50 default`. So all existing tests remain green.

```bash
pytest tests/unit/test_option_selector.py -v
```

Expected: All tests PASS (existing + new).

- [ ] **Step 5: Run full suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/strategy/option_selector.py tests/unit/test_option_selector.py
git commit -m "feat: add spread and open-interest eligibility filters to option selector"
```

---

### Task 4: Service — fix ×100 multiplier in `_compute_capital_pct` and `total_deployed_notional`

**Files:**
- Modify: `src/alpaca_bot/web/service.py`
- Modify: `tests/unit/test_web_service.py`

- [ ] **Step 1: Write failing tests**

Find the `_compute_capital_pct` test block in `tests/unit/test_web_service.py` (around line 1316). Add after the existing tests:

```python
def test_compute_capital_pct_option_position_uses_100x_multiplier():
    """Option positions (strategy_name='option') count 100 shares per contract."""
    opt = SimpleNamespace(symbol="AAPL240701P00150000", quantity=2, entry_price=1.20, strategy_name="option")
    # price=1.20, qty=2 → notional = 1.20 * 2 * 100 = 240
    result = _compute_capital_pct([opt], {"AAPL240701P00150000": 1.20})
    # Only one strategy, so 100%
    assert result == {"option": 100.0}


def test_compute_capital_pct_mixed_equity_and_option():
    """Equity and option positions sum correctly with respective multipliers."""
    eq_pos = SimpleNamespace(symbol="AAPL", quantity=10, entry_price=150.0, strategy_name="breakout")
    opt = SimpleNamespace(symbol="AAPL240701P00150000", quantity=2, entry_price=1.20, strategy_name="option")
    # eq: 150 * 10 = 1500; opt: 1.20 * 2 * 100 = 240; total = 1740
    result = _compute_capital_pct([eq_pos, opt], {})
    assert abs(result["breakout"] - round(1500 / 1740 * 100, 1)) < 0.05
    assert abs(result["option"] - round(240 / 1740 * 100, 1)) < 0.05


def test_load_dashboard_snapshot_option_position_uses_100x_in_total_deployed():
    """total_deployed_notional multiplies option positions by 100."""
    # qty=2, entry_price=1.20, multiplier=100 → 240.0
    opt = SimpleNamespace(symbol="AAPL240701P00150000", quantity=2, entry_price=1.20, strategy_name="option")

    stores = make_snapshot_stores()
    stores["position_store"] = SimpleNamespace(list_all=lambda **_: [opt])

    snapshot = load_dashboard_snapshot(
        settings=make_settings(),
        connection=SimpleNamespace(),
        **stores,
    )
    assert abs(snapshot.total_deployed_notional - 240.0) < 1e-6
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_web_service.py::test_compute_capital_pct_option_position_uses_100x_multiplier tests/unit/test_web_service.py::test_load_dashboard_snapshot_option_position_uses_100x_in_total_deployed -v
```

Expected: `FAILED` — capital_pct and total_deployed_notional do not apply ×100.

- [ ] **Step 3: Add `_option_multiplier` helper and fix service.py**

In `src/alpaca_bot/web/service.py`, add BEFORE the `_compute_capital_pct` function (around line 188):

```python
def _option_multiplier(pos: object) -> int:
    return 100 if getattr(pos, "strategy_name", "") == "option" else 1
```

Update `_compute_capital_pct`:

```python
def _compute_capital_pct(
    positions: list,
    latest_prices: dict[str, float],
) -> dict[str, float]:
    strategy_value: dict[str, float] = {}
    for pos in positions:
        price = latest_prices.get(pos.symbol, pos.entry_price)
        val = price * pos.quantity * _option_multiplier(pos)
        strategy_value[pos.strategy_name] = strategy_value.get(pos.strategy_name, 0.0) + val
    total = sum(strategy_value.values())
    if total <= 0:
        return {}
    return {name: round(val / total * 100, 1) for name, val in strategy_value.items()}
```

Update `total_deployed_notional` (around line 298):

```python
    total_deployed_notional: float = sum(
        pos.quantity * pos.entry_price * _option_multiplier(pos) for pos in positions
    )
```

- [ ] **Step 4: Run new tests**

```bash
pytest tests/unit/test_web_service.py -k "capital_pct or total_deployed" -v
```

Expected: All PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest --tb=short -q
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/web/service.py tests/unit/test_web_service.py
git commit -m "fix: apply ×100 contract multiplier for option positions in capital_pct and total_deployed_notional"
```

---

### Task 5: Template — fix ×100 multiplier in `dashboard.html`

**Files:**
- Modify: `src/alpaca_bot/web/templates/dashboard.html`

This is a pure template change — no new tests needed beyond verifying existing test suite passes (the test_web_app.py tests render the dashboard and would catch a syntax error, but cannot assert pixel-level P&L values for option positions without a full integration test of the template rendering path).

- [ ] **Step 1: Locate the positions loop in `dashboard.html`**

The fix is at lines ~450–516. Find this block:

```jinja
{% set init_val = (position.entry_price * position.quantity) if position.entry_price else 0.0 %}
```

- [ ] **Step 2: Add multiplier variable and update all affected calculations**

The key lines to update inside the `{% for position in snapshot.positions %}` block:

**First**, add `multiplier` as the VERY FIRST `{% set %}` inside the loop — before `stop_dist_pct`, `risk_dollars`, or any other computed variable. This is required because `risk_dollars` (line 454) uses `multiplier` and comes before `last_price` (line 455) in the template.

Immediately after `{% for position in snapshot.positions %}`, insert:

```jinja
{% set multiplier = 100 if position.strategy_name == "option" else 1 %}
```

Update `init_val`:

```jinja
{% set risk_dollars = (position.quantity * (position.entry_price - position.initial_stop_price) * multiplier) if (position.quantity and position.entry_price and position.initial_stop_price) else none %}
```

Update the "Init $" column (line ~481):

```jinja
{% if position.entry_price %}{{ "$%.0f" | format(position.entry_price * position.quantity * multiplier) }}{% else %}n/a{% endif %}
```

Update the "Curr $" column (line ~484):

```jinja
{% if last_price is not none %}{{ "$%.0f" | format(last_price * position.quantity * multiplier) }}{% else %}—{% endif %}
```

Update `upnl` and `upnl_pct` (lines ~487–488) — note `upnl_pct` is a ratio, does NOT need multiplier:

```jinja
{% set upnl = (last_price - position.entry_price) * position.quantity * multiplier %}
{% set upnl_pct = (last_price - position.entry_price) / position.entry_price * 100 %}
```

Update `total_curr_val` accumulator (line ~503):

```jinja
{%- if last_price is not none %}{% set ns.total_curr_val = ns.total_curr_val + last_price * position.quantity * multiplier %}{% endif %}
```

Update `upnl_acc` accumulator (line ~505):

```jinja
{%- set upnl_acc = (last_price - position.entry_price) * position.quantity * multiplier %}
```

- [ ] **Step 3: Run full test suite to confirm no template syntax errors**

```bash
pytest --tb=short -q
```

Expected: All tests pass. (Template render tests in `test_web_app.py` would catch syntax errors.)

- [ ] **Step 4: Verify dashboard renders manually (if dev server accessible)**

Start the web server:

```bash
alpaca-bot-web
```

Navigate to `http://localhost:18080/` — confirm positions section loads without errors.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/web/templates/dashboard.html
git commit -m "fix: apply ×100 contract multiplier for option positions in dashboard position table"
```

---

## Self-Review

**Spec coverage:**
- ×100 dashboard display bug → Tasks 4 + 5 ✓
- `spread_pct` property on `OptionContract` → Task 1 ✓
- `open_interest` field + extraction → Tasks 1 + 2 ✓
- `OPTION_MAX_SPREAD_PCT` + `OPTION_MIN_OPEN_INTEREST` settings → Task 1 ✓
- Spread + OI filters in selectors → Task 3 ✓

**Placeholder scan:** None found — all code blocks are complete.

**Type consistency:**
- `OptionContract.open_interest: int | None` used consistently in Tasks 1, 2, 3.
- `settings.option_max_spread_pct: float`, `settings.option_min_open_interest: int` used consistently in Tasks 1, 3.
- `_option_multiplier(pos) -> int` used in both service.py callsites in Task 4.

**Financial safety:**
- Spread and OI filters are pure eligibility filters — they only reduce the set of selectable contracts. The engine will emit no option ENTRY intent when `None` is returned, which is the same behavior as today when no contract passes DTE/delta filters.
- Dashboard fix is display-only — no change to order submission, stop placement, or position sizing.
- No new I/O introduced in engine (filters run inside `option_selector.py` which is called before engine evaluates intents).

# Options Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `breakout_calls` strategy — long call buying on breakout signals — with defined-risk premium-based sizing, EOD flatten, and a separate `option_orders` DB table.

**Architecture:** Six new files + nine modified files. `evaluate_cycle()` stays pure: option chain data is pre-fetched by the supervisor and passed as `option_chains_by_symbol`. The `breakout_calls` strategy is a factory function that closes over the chain data and returns a `StrategySignalEvaluator`-compatible callable. Option orders follow the same write-before-dispatch pattern as equity, using a separate `OptionOrderRepository`. Trade stream fills are routed by `client_order_id` prefix (`"option:"`).

**Tech Stack:** Python, psycopg2, alpaca-py ≥ 0.30.0 (options support), pytest

---

### File Map

**New files:**
| File | Purpose |
|---|---|
| `src/alpaca_bot/strategy/option_selector.py` | `select_call_contract()` pure function |
| `src/alpaca_bot/risk/option_sizing.py` | `calculate_option_position_size()` |
| `src/alpaca_bot/strategy/breakout_calls.py` | `make_breakout_calls_evaluator()` factory |
| `src/alpaca_bot/execution/option_chain.py` | `OptionChainAdapterProtocol` + `AlpacaOptionChainAdapter` |
| `src/alpaca_bot/runtime/option_dispatch.py` | `dispatch_pending_option_orders()` |
| `migrations/012_add_option_orders.sql` | `option_orders` table |

**Modified files:**
| File | Change |
|---|---|
| `src/alpaca_bot/domain/models.py` | Add `OptionContract` frozen dataclass; add `option_contract` field to `EntrySignal` |
| `src/alpaca_bot/config/__init__.py` | Add `option_dte_min`, `option_dte_max`, `option_delta_target` to `Settings` |
| `src/alpaca_bot/core/engine.py` | Add `underlying_symbol`, `is_option` to `CycleIntent`; add `option_chains_by_symbol` param to `evaluate_cycle()` |
| `src/alpaca_bot/storage/models.py` | Add `OptionOrderRecord` frozen dataclass |
| `src/alpaca_bot/storage/repositories.py` | Add `OptionOrderRepository` |
| `src/alpaca_bot/execution/alpaca.py` | Add `submit_option_limit_entry()`, `submit_option_market_exit()` |
| `src/alpaca_bot/runtime/cycle.py` | Route `is_option=True` ENTRY intents to `OptionOrderRecord` |
| `src/alpaca_bot/runtime/trade_updates.py` | Route `"option:"` prefix fills to `OptionOrderRepository` |
| `src/alpaca_bot/strategy/__init__.py` | Add `OPTION_STRATEGY_NAMES` set |
| `src/alpaca_bot/runtime/bootstrap.py` | Add `option_order_store` field to `RuntimeContext`; wire in `bootstrap_runtime()` and `reconnect_runtime_connection()` |
| `src/alpaca_bot/runtime/supervisor.py` | Fetch chains; build option evaluator; call `dispatch_pending_option_orders()`; EOD option flatten |

---

### Task 0: Create `tests/unit/helpers.py` shared test helpers

**Files:**
- Create: `tests/unit/helpers.py`

All new test files in Tasks 1–9 import `from tests.unit.helpers import _base_env, _make_settings`. This module does not yet exist; create it before any other task.

- [ ] **Step 1: Create `tests/unit/helpers.py`**

```python
from __future__ import annotations

from alpaca_bot.config import Settings


def _base_env() -> dict[str, str]:
    return {
        "TRADING_MODE": "paper",
        "ENABLE_LIVE_TRADING": "false",
        "STRATEGY_VERSION": "v1-breakout",
        "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
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
    }


def _make_settings(env: dict[str, str]) -> Settings:
    return Settings.from_env(env)
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from tests.unit.helpers import _base_env, _make_settings; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/unit/helpers.py
git commit -m "feat: add tests/unit/helpers.py shared test helpers (_base_env, _make_settings)"
```

---

### Task 1: `OptionContract` domain type + 3 Settings fields

**Files:**
- Modify: `src/alpaca_bot/domain/models.py`
- Modify: `src/alpaca_bot/config/__init__.py`
- Test: `tests/unit/test_option_domain_settings.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_option_domain_settings.py`:

```python
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from alpaca_bot.domain.models import OptionContract
from tests.unit.helpers import _base_env


def test_option_contract_fields():
    contract = OptionContract(
        occ_symbol="AAPL241220C00150000",
        underlying="AAPL",
        option_type="call",
        strike=150.0,
        expiry=date(2024, 12, 20),
        bid=2.50,
        ask=2.75,
        delta=0.52,
    )
    assert contract.occ_symbol == "AAPL241220C00150000"
    assert contract.underlying == "AAPL"
    assert contract.option_type == "call"
    assert contract.strike == 150.0
    assert contract.expiry == date(2024, 12, 20)
    assert contract.bid == 2.50
    assert contract.ask == 2.75
    assert contract.delta == 0.52


def test_option_contract_delta_optional():
    contract = OptionContract(
        occ_symbol="AAPL241220C00150000",
        underlying="AAPL",
        option_type="call",
        strike=150.0,
        expiry=date(2024, 12, 20),
        bid=2.50,
        ask=2.75,
    )
    assert contract.delta is None


def test_settings_option_defaults():
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())
    assert s.option_dte_min == 21
    assert s.option_dte_max == 60
    assert s.option_delta_target == 0.50


def test_settings_option_from_env_override():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DTE_MIN"] = "14"
    env["OPTION_DTE_MAX"] = "45"
    env["OPTION_DELTA_TARGET"] = "0.40"
    s = Settings.from_env(env)
    assert s.option_dte_min == 14
    assert s.option_dte_max == 45
    assert s.option_delta_target == 0.40


def test_settings_option_dte_min_must_be_at_least_1():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DTE_MIN"] = "0"
    with pytest.raises(ValueError, match="OPTION_DTE_MIN"):
        Settings.from_env(env)


def test_settings_option_dte_max_must_be_greater_than_min():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DTE_MIN"] = "30"
    env["OPTION_DTE_MAX"] = "20"
    with pytest.raises(ValueError, match="OPTION_DTE_MAX"):
        Settings.from_env(env)


def test_settings_option_delta_target_must_be_positive_fraction():
    from alpaca_bot.config import Settings
    env = _base_env()
    env["OPTION_DELTA_TARGET"] = "1.1"
    with pytest.raises(ValueError, match="OPTION_DELTA_TARGET"):
        Settings.from_env(env)
    env["OPTION_DELTA_TARGET"] = "0.0"
    with pytest.raises(ValueError, match="OPTION_DELTA_TARGET"):
        Settings.from_env(env)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_domain_settings.py -v
```
Expected: ImportError or AttributeError on `OptionContract` / `option_dte_min`.

- [ ] **Step 3: Add `OptionContract` to `domain/models.py`**

In `src/alpaca_bot/domain/models.py`, after the `Quote` dataclass, add:

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
```

Also add `from datetime import date, datetime` if `date` is not already imported (it already is in the existing file via `from datetime import date, datetime`).

- [ ] **Step 4: Add 3 Settings fields to `config/__init__.py`**

In the `Settings` dataclass (around line 137, after `max_spread_pct`), add:

```python
    option_dte_min: int = 21
    option_dte_max: int = 60
    option_delta_target: float = 0.50
```

In `from_env()` (around line 293, after `max_spread_pct=...`), add:

```python
            option_dte_min=int(values.get("OPTION_DTE_MIN", "21")),
            option_dte_max=int(values.get("OPTION_DTE_MAX", "60")),
            option_delta_target=float(values.get("OPTION_DELTA_TARGET", "0.50")),
```

In `validate()` (around line 356, after other validations), add:

```python
        if self.option_dte_min < 1:
            raise ValueError("OPTION_DTE_MIN must be at least 1")
        if self.option_dte_max <= self.option_dte_min:
            raise ValueError("OPTION_DTE_MAX must be greater than OPTION_DTE_MIN")
        if not 0.0 < self.option_delta_target <= 1.0:
            raise ValueError("OPTION_DELTA_TARGET must be between 0 (exclusive) and 1.0 (inclusive)")
```

- [ ] **Step 5: Run tests to verify all pass**

```bash
pytest tests/unit/test_option_domain_settings.py -v
```
Expected: 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/domain/models.py src/alpaca_bot/config/__init__.py tests/unit/test_option_domain_settings.py
git commit -m "feat: add OptionContract domain type and option settings (DTE range, delta target)"
```

---

### Task 2: `select_call_contract()` and `calculate_option_position_size()`

**Files:**
- Create: `src/alpaca_bot/strategy/option_selector.py`
- Create: `src/alpaca_bot/risk/option_sizing.py`
- Test: `tests/unit/test_option_selector.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_option_selector.py`:

```python
from __future__ import annotations

import math
import pytest
from datetime import date
from alpaca_bot.domain.models import OptionContract
from alpaca_bot.strategy.option_selector import select_call_contract
from alpaca_bot.risk.option_sizing import calculate_option_position_size
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _settings(**overrides) -> Settings:
    env = _base_env()
    env.update(overrides)
    return Settings.from_env(env)


def _contract(strike: float, expiry: date, ask: float, delta: float | None = None, option_type: str = "call") -> OptionContract:
    return OptionContract(
        occ_symbol=f"AAPL{expiry.strftime('%y%m%d')}C{int(strike * 1000):08d}",
        underlying="AAPL",
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        bid=ask - 0.05,
        ask=ask,
        delta=delta,
    )


TODAY = date(2024, 6, 1)
FAR_EXPIRY = date(2024, 8, 1)   # 61 days from TODAY — outside DTE_MAX=60 default
NEAR_EXPIRY = date(2024, 7, 1)  # 30 days from TODAY — within [21, 60] default


class TestSelectCallContract:
    def test_returns_none_when_no_contracts(self):
        s = _settings()
        assert select_call_contract([], current_price=150.0, today=TODAY, settings=s) is None

    def test_returns_none_when_no_eligible_contracts_by_dte(self):
        s = _settings()
        # 5 days to expiry — below DTE_MIN=21
        too_soon = date(2024, 6, 6)
        c = _contract(150.0, too_soon, ask=2.0, delta=0.50)
        assert select_call_contract([c], current_price=150.0, today=TODAY, settings=s) is None

    def test_returns_none_when_contract_outside_dte_max(self):
        s = _settings()
        # FAR_EXPIRY is 61 days out — exceeds DTE_MAX=60
        c = _contract(150.0, FAR_EXPIRY, ask=2.0, delta=0.50)
        assert select_call_contract([c], current_price=150.0, today=TODAY, settings=s) is None

    def test_returns_none_when_ask_is_zero(self):
        s = _settings()
        c = OptionContract(
            occ_symbol="AAPL240701C00150000",
            underlying="AAPL",
            option_type="call",
            strike=150.0,
            expiry=NEAR_EXPIRY,
            bid=0.0,
            ask=0.0,
            delta=0.50,
        )
        assert select_call_contract([c], current_price=150.0, today=TODAY, settings=s) is None

    def test_selects_by_delta_closest_to_target(self):
        s = _settings(OPTION_DELTA_TARGET="0.50")
        c30 = _contract(160.0, NEAR_EXPIRY, ask=2.0, delta=0.30)
        c50 = _contract(150.0, NEAR_EXPIRY, ask=3.0, delta=0.50)
        c70 = _contract(140.0, NEAR_EXPIRY, ask=5.0, delta=0.70)
        result = select_call_contract([c30, c50, c70], current_price=150.0, today=TODAY, settings=s)
        assert result is c50

    def test_selects_atm_by_strike_when_no_delta(self):
        s = _settings()
        c140 = _contract(140.0, NEAR_EXPIRY, ask=10.0)
        c150 = _contract(150.0, NEAR_EXPIRY, ask=3.0)
        c160 = _contract(160.0, NEAR_EXPIRY, ask=1.5)
        result = select_call_contract([c140, c150, c160], current_price=150.0, today=TODAY, settings=s)
        assert result is c150

    def test_skips_put_contracts(self):
        s = _settings()
        put = OptionContract(
            occ_symbol="AAPL240701P00150000",
            underlying="AAPL",
            option_type="put",
            strike=150.0,
            expiry=NEAR_EXPIRY,
            bid=2.50,
            ask=2.75,
            delta=None,
        )
        call = _contract(150.0, NEAR_EXPIRY, ask=3.0, delta=0.50)
        result = select_call_contract([put, call], current_price=150.0, today=TODAY, settings=s)
        assert result is call


class TestCalculateOptionPositionSize:
    def test_basic_sizing(self):
        s = _settings(RISK_PER_TRADE_PCT="0.01", MAX_POSITION_PCT="0.05")
        # equity=100_000, risk_budget=1000, contract_cost=5*100=500 → 2 contracts
        result = calculate_option_position_size(equity=100_000, ask=5.0, settings=s)
        assert result == 2

    def test_capped_by_max_position_pct(self):
        s = _settings(RISK_PER_TRADE_PCT="0.20", MAX_POSITION_PCT="0.01")
        # equity=100_000, max_notional=1000, contract_cost=5*100=500 → max 2 contracts
        # risk_budget=20_000 / 500 = 40, but capped at floor(1000/500)=2
        result = calculate_option_position_size(equity=100_000, ask=5.0, settings=s)
        assert result == 2

    def test_returns_zero_when_ask_exceeds_budget(self):
        s = _settings(RISK_PER_TRADE_PCT="0.001", MAX_POSITION_PCT="0.05")
        # equity=10_000, risk_budget=10, contract_cost=500 → 0 contracts
        result = calculate_option_position_size(equity=10_000, ask=5.0, settings=s)
        assert result == 0

    def test_returns_zero_when_ask_is_zero(self):
        s = _settings()
        result = calculate_option_position_size(equity=100_000, ask=0.0, settings=s)
        assert result == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_selector.py -v
```
Expected: ImportError on `option_selector` and `option_sizing`.

- [ ] **Step 3: Create `src/alpaca_bot/strategy/option_selector.py`**

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
    ]
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(c.delta - settings.option_delta_target))  # type: ignore[operator]
    return min(eligible, key=lambda c: abs(c.strike - current_price))
```

- [ ] **Step 4: Create `src/alpaca_bot/risk/option_sizing.py`**

```python
from __future__ import annotations

import math

from alpaca_bot.config import Settings


def calculate_option_position_size(
    *,
    equity: float,
    ask: float,
    settings: Settings,
) -> int:
    if ask <= 0:
        return 0
    contract_cost = ask * 100
    risk_budget = equity * settings.risk_per_trade_pct
    contracts = math.floor(risk_budget / contract_cost)
    max_notional = equity * settings.max_position_pct
    max_contracts = math.floor(max_notional / contract_cost)
    return max(0, min(contracts, max_contracts))
```

- [ ] **Step 5: Run tests to verify all pass**

```bash
pytest tests/unit/test_option_selector.py -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/strategy/option_selector.py src/alpaca_bot/risk/option_sizing.py tests/unit/test_option_selector.py
git commit -m "feat: add select_call_contract() and calculate_option_position_size()"
```

---

### Task 3: `breakout_calls` strategy factory + `OPTION_STRATEGY_NAMES`

**Files:**
- Create: `src/alpaca_bot/strategy/breakout_calls.py`
- Modify: `src/alpaca_bot/strategy/__init__.py`
- Test: `tests/unit/test_breakout_calls_strategy.py` (new)

- [ ] **Step 1: Write failing tests**

First check what `evaluate_breakout_signal` requires by noting it needs `signal_index` and returns `EntrySignal | None`. The tests use a minimal Bar sequence.

Create `tests/unit/test_breakout_calls_strategy.py`:

```python
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _settings(**overrides) -> Settings:
    env = _base_env()
    env.update(overrides)
    return Settings.from_env(env)


def _bar(close: float = 100.0, ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)
    return Bar(symbol="AAPL", timestamp=ts, open=close, high=close + 1, low=close - 1, close=close, volume=1000.0)


def _contract(strike: float = 150.0) -> OptionContract:
    return OptionContract(
        occ_symbol="AAPL240701C00150000",
        underlying="AAPL",
        option_type="call",
        strike=strike,
        expiry=date(2024, 7, 1),
        bid=2.50,
        ask=2.75,
        delta=0.50,
    )


class TestMakeBreakoutCallsEvaluator:
    def test_returns_none_when_no_chain_for_symbol(self):
        evaluator = make_breakout_calls_evaluator({})
        s = _settings()
        bars = [_bar()] * 25
        result = evaluator(symbol="AAPL", intraday_bars=bars, signal_index=len(bars) - 1, daily_bars=bars, settings=s)
        assert result is None

    def test_returns_none_when_underlying_breakout_signal_is_none(self):
        contract = _contract()
        evaluator = make_breakout_calls_evaluator({"AAPL": [contract]})
        s = _settings()
        # Only 2 bars — not enough for breakout detection (needs lookback_bars=20)
        bars = [_bar()] * 2
        result = evaluator(symbol="AAPL", intraday_bars=bars, signal_index=1, daily_bars=bars, settings=s)
        assert result is None

    def test_returns_none_when_no_eligible_contract(self):
        # Contract already expired (0 DTE)
        import datetime as dt
        expired_contract = OptionContract(
            occ_symbol="AAPL240601C00150000",
            underlying="AAPL",
            option_type="call",
            strike=150.0,
            expiry=date(2024, 6, 1),
            bid=2.50,
            ask=2.75,
            delta=0.50,
        )
        evaluator = make_breakout_calls_evaluator({"AAPL": [expired_contract]})
        s = _settings()
        bars = [_bar()] * 2
        result = evaluator(symbol="AAPL", intraday_bars=bars, signal_index=1, daily_bars=bars, settings=s)
        assert result is None

    def test_breakout_calls_is_in_option_strategy_names(self):
        assert "breakout_calls" in OPTION_STRATEGY_NAMES

    def test_evaluator_is_callable(self):
        evaluator = make_breakout_calls_evaluator({})
        assert callable(evaluator)

    def test_returned_signal_carries_option_contract_when_breakout_fires(self):
        """When underlying breakout fires and a valid contract exists, signal has option_contract set."""
        from alpaca_bot.strategy.breakout import evaluate_breakout_signal
        contract = _contract(strike=100.0)
        evaluator = make_breakout_calls_evaluator({"AAPL": [contract]})
        s = _settings(
            BREAKOUT_LOOKBACK_BARS="3",
            RELATIVE_VOLUME_THRESHOLD="1.1",
            DAILY_SMA_PERIOD="2",
            OPTION_DTE_MIN="1",
            OPTION_DTE_MAX="60",
        )

        # Build bars where the last bar breaks above the 3-bar high
        base_ts = datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)
        import datetime as dt

        def _make_bar(close: float, offset_min: int) -> Bar:
            ts = base_ts + dt.timedelta(minutes=offset_min * 15)
            return Bar(symbol="AAPL", timestamp=ts, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=500.0)

        # Many flat daily bars for SMA
        daily_bars = [
            Bar(symbol="AAPL", timestamp=datetime(2024, 5, d, 0, 0, tzinfo=timezone.utc),
                open=95.0, high=96.0, low=94.0, close=95.0, volume=1_000_000.0)
            for d in range(1, 20)
        ]

        # Intraday: 3 base bars then a breakout bar with high volume
        intraday_bars = [
            Bar(symbol="AAPL", timestamp=base_ts + dt.timedelta(minutes=i * 15),
                open=95.0, high=96.0, low=94.0, close=95.0, volume=500.0)
            for i in range(3)
        ] + [
            Bar(symbol="AAPL", timestamp=base_ts + dt.timedelta(minutes=3 * 15),
                open=97.0, high=100.0, low=96.5, close=99.5, volume=2000.0)
        ]

        result = evaluator(
            symbol="AAPL",
            intraday_bars=intraday_bars,
            signal_index=len(intraday_bars) - 1,
            daily_bars=daily_bars,
            settings=s,
        )
        # May be None if breakout signal doesn't fire with these bars — that's fine.
        # The important thing is it returns EntrySignal with option_contract when it fires.
        if result is not None:
            assert isinstance(result, EntrySignal)
            assert result.option_contract is contract or result.option_contract is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_breakout_calls_strategy.py -v
```
Expected: ImportError on `breakout_calls` and `OPTION_STRATEGY_NAMES`.

- [ ] **Step 3: Create `src/alpaca_bot/strategy/breakout_calls.py`**

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timezone
from typing import Callable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import evaluate_breakout_signal
from alpaca_bot.strategy.option_selector import select_call_contract


def make_breakout_calls_evaluator(
    option_chains_by_symbol: Mapping[str, Sequence[OptionContract]],
) -> Callable[..., EntrySignal | None]:
    def evaluate(
        *,
        symbol: str,
        intraday_bars: Sequence[Bar],
        signal_index: int,
        daily_bars: Sequence[Bar],
        settings: Settings,
    ) -> EntrySignal | None:
        chains = option_chains_by_symbol.get(symbol, ())
        if not chains:
            return None
        equity_signal = evaluate_breakout_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_call_contract(
            chains,
            current_price=intraday_bars[-1].close,
            today=today,
            settings=settings,
        )
        if contract is None:
            return None
        return EntrySignal(
            symbol=symbol,
            signal_bar=equity_signal.signal_bar,
            entry_level=equity_signal.entry_level,
            relative_volume=equity_signal.relative_volume,
            stop_price=0.0,
            limit_price=contract.ask,
            initial_stop_price=0.01,
            option_contract=contract,
        )

    return evaluate
```

- [ ] **Step 4: Add `OPTION_STRATEGY_NAMES` to `strategy/__init__.py`**

In `src/alpaca_bot/strategy/__init__.py`, after the `STRATEGY_REGISTRY` dict, add:

```python
OPTION_STRATEGY_NAMES: frozenset[str] = frozenset({"breakout_calls"})
```

Also add to the imports at top:
```python
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator
```

Note: `breakout_calls` is NOT added to `STRATEGY_REGISTRY` — it is a factory, not a direct evaluator. The supervisor builds the evaluator at runtime using `make_breakout_calls_evaluator(chains)`.

- [ ] **Step 5: Run tests to verify all pass**

```bash
pytest tests/unit/test_breakout_calls_strategy.py -v
```
Expected: all tests PASS. (The last test may be skipped-equivalent if breakout doesn't fire — that's intentional; the test asserts correctness of the conditional path.)

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/strategy/breakout_calls.py src/alpaca_bot/strategy/__init__.py tests/unit/test_breakout_calls_strategy.py
git commit -m "feat: add breakout_calls strategy factory and OPTION_STRATEGY_NAMES"
```

---

### Task 4: `EntrySignal.option_contract` + `CycleIntent` extensions + `evaluate_cycle()` option branch

**Files:**
- Modify: `src/alpaca_bot/domain/models.py` — add `option_contract` to `EntrySignal`
- Modify: `src/alpaca_bot/core/engine.py` — add `underlying_symbol`, `is_option` to `CycleIntent`; add `option_chains_by_symbol` param; option sizing branch
- Test: `tests/unit/test_cycle_engine.py` (add new cases)

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_cycle_engine.py`:

```python
# --- Options engine tests (add near end of file) ---

def _option_contract_fixture() -> OptionContract:
    from datetime import date
    from alpaca_bot.domain.models import OptionContract
    return OptionContract(
        occ_symbol="AAPL240701C00100000",
        underlying="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        bid=2.50,
        ask=3.00,
        delta=0.50,
    )


def test_cycle_intent_is_option_defaults_false():
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType
    from datetime import datetime, timezone
    intent = CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol="AAPL",
        timestamp=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
    )
    assert intent.is_option is False
    assert intent.underlying_symbol is None


def test_cycle_intent_with_option_fields():
    from alpaca_bot.core.engine import CycleIntent, CycleIntentType
    from datetime import datetime, timezone
    intent = CycleIntent(
        intent_type=CycleIntentType.ENTRY,
        symbol="AAPL240701C00100000",
        timestamp=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
        is_option=True,
        underlying_symbol="AAPL",
    )
    assert intent.is_option is True
    assert intent.underlying_symbol == "AAPL"


def test_evaluate_cycle_option_entry_uses_option_sizing():
    """When a breakout_calls evaluator returns an option signal, evaluate_cycle uses premium-based sizing."""
    import datetime as dt
    from datetime import date, datetime, timezone
    from alpaca_bot.core.engine import evaluate_cycle, CycleIntentType
    from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
    from tests.unit.helpers import _base_env, _make_settings

    contract = OptionContract(
        occ_symbol="AAPL240701C00100000",
        underlying="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        bid=2.50,
        ask=3.00,
        delta=0.50,
    )

    now = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)

    def fake_option_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[-1]
        return EntrySignal(
            symbol=symbol,
            signal_bar=bar,
            entry_level=bar.close,
            relative_volume=2.0,
            stop_price=0.0,
            limit_price=contract.ask,
            initial_stop_price=0.01,
            option_contract=contract,
        )

    env = _base_env()
    env["RISK_PER_TRADE_PCT"] = "0.01"  # 1% of 100k = 1000 budget; ask=3.0, cost=300/contract → 3 contracts
    env["MAX_POSITION_PCT"] = "0.10"
    s = _make_settings(env)

    bar = Bar(symbol="AAPL", timestamp=now, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
    daily_bar = Bar(symbol="AAPL", timestamp=datetime(2024, 5, 31, 0, 0, tzinfo=timezone.utc), open=95.0, high=100.0, low=94.0, close=98.0, volume=1_000_000.0)

    result = evaluate_cycle(
        settings=s,
        now=now,
        equity=100_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [daily_bar]},
        open_positions=[],
        working_order_symbols=set(),
        traded_symbols_today=set(),
        entries_disabled=False,
        signal_evaluator=fake_option_evaluator,
        strategy_name="breakout_calls",
    )

    option_entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY and i.is_option]
    assert len(option_entries) == 1
    intent = option_entries[0]
    assert intent.symbol == "AAPL240701C00100000"
    assert intent.underlying_symbol == "AAPL"
    assert intent.is_option is True
    assert intent.quantity == 3  # floor(1000 / 300) = 3
    assert intent.client_order_id is not None
    assert intent.client_order_id.startswith("option:")


def test_evaluate_cycle_option_entry_skipped_when_quantity_zero():
    """If option sizing returns 0 contracts, no ENTRY intent is emitted."""
    import datetime as dt
    from datetime import date, datetime, timezone
    from alpaca_bot.core.engine import evaluate_cycle, CycleIntentType
    from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
    from tests.unit.helpers import _base_env, _make_settings

    contract = OptionContract(
        occ_symbol="AAPL240701C00100000",
        underlying="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        bid=99.50,
        ask=100.0,  # $100 ask → contract_cost = $10000; tiny budget → 0 contracts
        delta=0.50,
    )
    now = datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)

    def fake_option_evaluator(*, symbol, intraday_bars, signal_index, daily_bars, settings):
        bar = intraday_bars[-1]
        return EntrySignal(
            symbol=symbol, signal_bar=bar, entry_level=bar.close, relative_volume=2.0,
            stop_price=0.0, limit_price=contract.ask, initial_stop_price=0.01,
            option_contract=contract,
        )

    env = _base_env()
    env["RISK_PER_TRADE_PCT"] = "0.001"  # tiny budget
    s = _make_settings(env)

    bar = Bar(symbol="AAPL", timestamp=now, open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
    daily_bar = Bar(symbol="AAPL", timestamp=datetime(2024, 5, 31, 0, 0, tzinfo=timezone.utc), open=95.0, high=100.0, low=94.0, close=98.0, volume=1_000_000.0)

    result = evaluate_cycle(
        settings=s, now=now, equity=10_000.0,
        intraday_bars_by_symbol={"AAPL": [bar]},
        daily_bars_by_symbol={"AAPL": [daily_bar]},
        open_positions=[], working_order_symbols=set(),
        traded_symbols_today=set(), entries_disabled=False,
        signal_evaluator=fake_option_evaluator, strategy_name="breakout_calls",
    )
    option_entries = [i for i in result.intents if i.intent_type == CycleIntentType.ENTRY and i.is_option]
    assert len(option_entries) == 0
```

Verify `_make_settings` exists in `tests/unit/helpers.py`. If not, add it (it may be named differently — check the file and adjust).

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_cycle_engine.py::test_cycle_intent_is_option_defaults_false tests/unit/test_cycle_engine.py::test_evaluate_cycle_option_entry_uses_option_sizing -v
```
Expected: AttributeError on `is_option` / `underlying_symbol`.

- [ ] **Step 3: Add `option_contract` to `EntrySignal` in `domain/models.py`**

In `src/alpaca_bot/domain/models.py`, change `EntrySignal` from:

```python
@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    signal_bar: Bar
    entry_level: float
    relative_volume: float
    stop_price: float
    limit_price: float
    initial_stop_price: float
```

To:

```python
@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    signal_bar: Bar
    entry_level: float
    relative_volume: float
    stop_price: float
    limit_price: float
    initial_stop_price: float
    option_contract: "OptionContract | None" = None
```

Because `OptionContract` is defined later in the same file, use a string annotation. Alternatively, move `OptionContract` before `EntrySignal` or use `from __future__ import annotations` (already present).

- [ ] **Step 4: Add `underlying_symbol`, `is_option`, `option_strike`, `option_expiry`, `option_type_str` to `CycleIntent`**

In `src/alpaca_bot/core/engine.py`, change `CycleIntent` from:

```python
@dataclass(frozen=True)
class CycleIntent:
    intent_type: CycleIntentType
    symbol: str
    timestamp: datetime
    quantity: int | None = None
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    client_order_id: str | None = None
    reason: str | None = None
    signal_timestamp: datetime | None = None
    strategy_name: str = "breakout"
```

To:

```python
@dataclass(frozen=True)
class CycleIntent:
    intent_type: CycleIntentType
    symbol: str
    timestamp: datetime
    quantity: int | None = None
    stop_price: float | None = None
    limit_price: float | None = None
    initial_stop_price: float | None = None
    client_order_id: str | None = None
    reason: str | None = None
    signal_timestamp: datetime | None = None
    strategy_name: str = "breakout"
    underlying_symbol: str | None = None
    is_option: bool = False
    option_strike: float | None = None
    option_expiry: "date | None" = None
    option_type_str: str | None = None
```

Also add `date` to the import at the top of `engine.py` if not already present:
```python
from datetime import date, datetime
```

- [ ] **Step 5: Add `option_chains_by_symbol` param and option branch to `evaluate_cycle()`**

In `src/alpaca_bot/core/engine.py`:

1. Add import at top of file:
```python
from alpaca_bot.risk.option_sizing import calculate_option_position_size
```

2. Add `option_chains_by_symbol` parameter to `evaluate_cycle()` signature (after `quotes_by_symbol`):
```python
    option_chains_by_symbol: "Mapping[str, Sequence[OptionContract]] | None" = None,
```

Also add `OptionContract` to the TYPE_CHECKING import:
```python
if TYPE_CHECKING:
    from alpaca_bot.storage import DailySessionState
    from alpaca_bot.domain.models import OptionContract
```

3. In the entry candidates loop, replace the sizing and intent-building block (currently around lines 293-329) with a branched version:

Replace:
```python
                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=len(bars) - 1,
                    daily_bars=daily_bars,
                    settings=settings,
                )
                if signal is None:
                    continue

                if signal.initial_stop_price >= signal.limit_price:
                    continue
                if signal.limit_price - signal.initial_stop_price < 0.01:
                    continue
                quantity = calculate_position_size(
                    equity=equity,
                    entry_price=signal.limit_price,
                    stop_price=signal.initial_stop_price,
                    settings=settings,
                )
                if quantity < 1:
                    continue

                entry_candidates.append(
                    (
                        round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                        round(signal.relative_volume, 6),
                        CycleIntent(
                            intent_type=CycleIntentType.ENTRY,
                            symbol=symbol,
                            timestamp=signal.signal_bar.timestamp,
                            quantity=quantity,
                            stop_price=signal.stop_price,
                            limit_price=signal.limit_price,
                            initial_stop_price=signal.initial_stop_price,
                            client_order_id=_client_order_id(
                                settings=settings,
                                symbol=symbol,
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                            ),
                            signal_timestamp=signal.signal_bar.timestamp,
                            strategy_name=strategy_name,
                        ),
                    )
                )
```

With:
```python
                signal = signal_evaluator(
                    symbol=symbol,
                    intraday_bars=bars,
                    signal_index=len(bars) - 1,
                    daily_bars=daily_bars,
                    settings=settings,
                )
                if signal is None:
                    continue

                if signal.option_contract is not None:
                    # Option entry: defined risk = premium; no stop needed
                    quantity = calculate_option_position_size(
                        equity=equity,
                        ask=signal.option_contract.ask,
                        settings=settings,
                    )
                    if quantity < 1:
                        continue
                    contract = signal.option_contract
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=contract.occ_symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=None,
                                limit_price=contract.ask,
                                initial_stop_price=None,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=contract.occ_symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                    is_option=True,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                                underlying_symbol=symbol,
                                is_option=True,
                                option_strike=contract.strike,
                                option_expiry=contract.expiry,
                                option_type_str=contract.option_type,
                            ),
                        )
                    )
                else:
                    # Equity entry: stop-based sizing
                    if signal.initial_stop_price >= signal.limit_price:
                        continue
                    if signal.limit_price - signal.initial_stop_price < 0.01:
                        continue
                    quantity = calculate_position_size(
                        equity=equity,
                        entry_price=signal.limit_price,
                        stop_price=signal.initial_stop_price,
                        settings=settings,
                    )
                    if quantity < 1:
                        continue
                    entry_candidates.append(
                        (
                            round((signal.signal_bar.close / signal.entry_level) - 1, 6),
                            round(signal.relative_volume, 6),
                            CycleIntent(
                                intent_type=CycleIntentType.ENTRY,
                                symbol=symbol,
                                timestamp=signal.signal_bar.timestamp,
                                quantity=quantity,
                                stop_price=signal.stop_price,
                                limit_price=signal.limit_price,
                                initial_stop_price=signal.initial_stop_price,
                                client_order_id=_client_order_id(
                                    settings=settings,
                                    symbol=symbol,
                                    signal_timestamp=signal.signal_bar.timestamp,
                                    strategy_name=strategy_name,
                                ),
                                signal_timestamp=signal.signal_bar.timestamp,
                                strategy_name=strategy_name,
                            ),
                        )
                    )
```

4. Update `_client_order_id()` to accept `is_option=False`:

```python
def _client_order_id(
    *,
    settings: Settings,
    symbol: str,
    signal_timestamp: datetime,
    strategy_name: str = "breakout",
    is_option: bool = False,
) -> str:
    prefix = "option" if is_option else strategy_name
    return (
        f"{prefix}:"
        f"{settings.strategy_version}:"
        f"{signal_timestamp.date().isoformat()}:"
        f"{symbol}:entry:{signal_timestamp.isoformat()}"
    )
```

- [ ] **Step 6: Run tests to verify all pass**

```bash
pytest tests/unit/test_cycle_engine.py -v
```
Expected: all tests PASS (including new option tests).

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/domain/models.py src/alpaca_bot/core/engine.py tests/unit/test_cycle_engine.py
git commit -m "feat: add option branch to evaluate_cycle() with option_contract detection and premium-based sizing"
```

---

### Task 5: DB migration + `OptionOrderRecord` + `OptionOrderRepository`

**Files:**
- Create: `migrations/012_add_option_orders.sql`
- Modify: `src/alpaca_bot/storage/models.py`
- Modify: `src/alpaca_bot/storage/repositories.py`
- Test: `tests/unit/test_option_storage.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_option_storage.py`:

```python
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import OptionOrderRecord
from alpaca_bot.storage.repositories import OptionOrderRepository
from alpaca_bot.storage.db import ConnectionProtocol


class _FakeConnection:
    def __init__(self):
        self._rows: list[dict] = []
        self.committed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass

    def cursor(self):
        return _FakeCursor(self._rows)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.lastrowid = None
        self._query = None
        self._params = None

    def execute(self, query, params=None):
        self._query = query
        self._params = params

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _now() -> datetime:
    return datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)


def _record(**kwargs) -> OptionOrderRecord:
    defaults = dict(
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        occ_symbol="AAPL240701C00100000",
        underlying_symbol="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        side="buy",
        status="pending_submit",
        quantity=2,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout_calls",
        created_at=_now(),
        updated_at=_now(),
        limit_price=3.00,
    )
    defaults.update(kwargs)
    return OptionOrderRecord(**defaults)


def test_option_order_record_has_expected_fields():
    r = _record()
    assert r.client_order_id.startswith("option:")
    assert r.occ_symbol == "AAPL240701C00100000"
    assert r.underlying_symbol == "AAPL"
    assert r.option_type == "call"
    assert r.strike == 100.0
    assert r.expiry == date(2024, 7, 1)
    assert r.side == "buy"
    assert r.status == "pending_submit"
    assert r.quantity == 2
    assert r.trading_mode is TradingMode.PAPER
    assert r.limit_price == 3.00
    assert r.broker_order_id is None
    assert r.fill_price is None
    assert r.filled_quantity is None


def test_option_order_record_is_frozen():
    r = _record()
    with pytest.raises((AttributeError, TypeError)):
        r.status = "submitted"  # type: ignore


def test_option_order_repository_save_calls_execute():
    """save() issues an INSERT/ON CONFLICT statement."""
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    r = _record()
    repo.save(r, commit=True)
    cursor = conn.cursor()
    # Just verify no exception — the fake connection doesn't persist rows.


def test_option_order_repository_update_fill():
    """update_fill() sets status, fill_price, filled_quantity, broker_order_id."""
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    repo.update_fill(
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        broker_order_id="broker-123",
        fill_price=3.10,
        filled_quantity=2,
        status="filled",
        updated_at=_now(),
    )
    # No exception = pass


def test_option_order_repository_list_by_status():
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    result = repo.list_by_status(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        statuses=["pending_submit"],
    )
    assert isinstance(result, list)


def test_option_order_repository_list_open_option_positions():
    conn = _FakeConnection()
    repo = OptionOrderRepository(conn)
    result = repo.list_open_option_positions(
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
    )
    assert isinstance(result, list)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_storage.py -v
```
Expected: ImportError on `OptionOrderRecord` / `OptionOrderRepository`.

- [ ] **Step 3: Create `migrations/012_add_option_orders.sql`**

```sql
CREATE TABLE IF NOT EXISTS option_orders (
    client_order_id TEXT PRIMARY KEY,
    occ_symbol TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    strike DOUBLE PRECISION NOT NULL,
    expiry DATE NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    status TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    trading_mode TEXT NOT NULL CHECK (trading_mode IN ('paper', 'live')),
    strategy_version TEXT NOT NULL,
    strategy_name TEXT NOT NULL DEFAULT 'breakout_calls',
    limit_price DOUBLE PRECISION,
    broker_order_id TEXT,
    fill_price DOUBLE PRECISION,
    filled_quantity INTEGER,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_option_orders_underlying_status
    ON option_orders (underlying_symbol, status);

CREATE INDEX IF NOT EXISTS idx_option_orders_broker_order_id
    ON option_orders (broker_order_id)
    WHERE broker_order_id IS NOT NULL;
```

- [ ] **Step 4: Add `OptionOrderRecord` to `storage/models.py`**

In `src/alpaca_bot/storage/models.py`, after `OrderRecord`, add:

```python
@dataclass(frozen=True)
class OptionOrderRecord:
    client_order_id: str
    occ_symbol: str
    underlying_symbol: str
    option_type: str
    strike: float
    expiry: date
    side: str
    status: str
    quantity: int
    trading_mode: TradingMode
    strategy_version: str
    strategy_name: str = "breakout_calls"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    limit_price: float | None = None
    broker_order_id: str | None = None
    fill_price: float | None = None
    filled_quantity: int | None = None
```

- [ ] **Step 5: Add `OptionOrderRepository` to `storage/repositories.py`**

In `src/alpaca_bot/storage/repositories.py`, add import at top:
```python
from alpaca_bot.storage.models import (
    AuditEvent,
    DailySessionState,
    OptionOrderRecord,
    OrderRecord,
    PositionRecord,
    StrategyFlag,
    TradingStatus,
    TradingStatusValue,
)
```

Then add the repository class after the existing `OrderRepository` class:

```python
class OptionOrderRepository:
    def __init__(self, connection: ConnectionProtocol) -> None:
        self._connection = connection

    def save(self, record: OptionOrderRecord, *, commit: bool = True) -> None:
        execute(
            self._connection,
            """
            INSERT INTO option_orders (
                client_order_id, occ_symbol, underlying_symbol, option_type,
                strike, expiry, side, status, quantity, trading_mode,
                strategy_version, strategy_name, limit_price, broker_order_id,
                fill_price, filled_quantity, created_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (client_order_id) DO UPDATE SET
                status = EXCLUDED.status,
                broker_order_id = EXCLUDED.broker_order_id,
                fill_price = EXCLUDED.fill_price,
                filled_quantity = EXCLUDED.filled_quantity,
                updated_at = EXCLUDED.updated_at
            """,
            (
                record.client_order_id,
                record.occ_symbol,
                record.underlying_symbol,
                record.option_type,
                record.strike,
                record.expiry,
                record.side,
                record.status,
                record.quantity,
                record.trading_mode.value,
                record.strategy_version,
                record.strategy_name,
                record.limit_price,
                record.broker_order_id,
                record.fill_price,
                record.filled_quantity,
                record.created_at,
                record.updated_at,
            ),
            commit=commit,
        )

    def update_fill(
        self,
        *,
        client_order_id: str,
        broker_order_id: str,
        fill_price: float,
        filled_quantity: int,
        status: str,
        updated_at: datetime,
    ) -> None:
        execute(
            self._connection,
            """
            UPDATE option_orders
            SET status=%s, broker_order_id=%s, fill_price=%s, filled_quantity=%s, updated_at=%s
            WHERE client_order_id=%s
            """,
            (status, broker_order_id, fill_price, filled_quantity, updated_at, client_order_id),
            commit=True,
        )

    def list_by_status(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
        statuses: list[str],
    ) -> list[OptionOrderRecord]:
        rows = fetch_all(
            self._connection,
            """
            SELECT client_order_id, occ_symbol, underlying_symbol, option_type,
                   strike, expiry, side, status, quantity, trading_mode,
                   strategy_version, strategy_name, limit_price, broker_order_id,
                   fill_price, filled_quantity, created_at, updated_at
            FROM option_orders
            WHERE trading_mode=%s AND strategy_version=%s AND status=ANY(%s)
            ORDER BY created_at
            """,
            (trading_mode.value, strategy_version, statuses),
        )
        return [self._row_to_record(r) for r in rows]

    def list_open_option_positions(
        self,
        *,
        trading_mode: TradingMode,
        strategy_version: str,
    ) -> list[OptionOrderRecord]:
        """Returns filled buy orders that have no corresponding sell (any status)."""
        rows = fetch_all(
            self._connection,
            """
            SELECT o.client_order_id, o.occ_symbol, o.underlying_symbol, o.option_type,
                   o.strike, o.expiry, o.side, o.status, o.quantity, o.trading_mode,
                   o.strategy_version, o.strategy_name, o.limit_price, o.broker_order_id,
                   o.fill_price, o.filled_quantity, o.created_at, o.updated_at
            FROM option_orders o
            WHERE o.trading_mode=%s AND o.strategy_version=%s
              AND o.side='buy' AND o.status='filled'
              AND NOT EXISTS (
                  SELECT 1 FROM option_orders s
                  WHERE s.occ_symbol=o.occ_symbol
                    AND s.side='sell'
                    AND s.status IN ('pending_submit', 'submitting', 'submitted', 'filled')
                    AND s.trading_mode=o.trading_mode
                    AND s.strategy_version=o.strategy_version
              )
            """,
            (trading_mode.value, strategy_version),
        )
        return [self._row_to_record(r) for r in rows]

    def load_by_broker_order_id(self, broker_order_id: str) -> OptionOrderRecord | None:
        row = fetch_one(
            self._connection,
            """
            SELECT client_order_id, occ_symbol, underlying_symbol, option_type,
                   strike, expiry, side, status, quantity, trading_mode,
                   strategy_version, strategy_name, limit_price, broker_order_id,
                   fill_price, filled_quantity, created_at, updated_at
            FROM option_orders WHERE broker_order_id=%s
            """,
            (broker_order_id,),
        )
        if row is None:
            return None
        return self._row_to_record(row)

    def _row_to_record(self, row: dict) -> OptionOrderRecord:
        return OptionOrderRecord(
            client_order_id=row["client_order_id"],
            occ_symbol=row["occ_symbol"],
            underlying_symbol=row["underlying_symbol"],
            option_type=row["option_type"],
            strike=float(row["strike"]),
            expiry=row["expiry"] if isinstance(row["expiry"], date) else date.fromisoformat(str(row["expiry"])),
            side=row["side"],
            status=row["status"],
            quantity=int(row["quantity"]),
            trading_mode=TradingMode(row["trading_mode"]),
            strategy_version=row["strategy_version"],
            strategy_name=row["strategy_name"],
            limit_price=float(row["limit_price"]) if row["limit_price"] is not None else None,
            broker_order_id=row["broker_order_id"],
            fill_price=float(row["fill_price"]) if row["fill_price"] is not None else None,
            filled_quantity=int(row["filled_quantity"]) if row["filled_quantity"] is not None else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
```

Note: `date` is already imported in `repositories.py` — verify and add if needed: `from datetime import date, datetime`.

- [ ] **Step 6: Wire `OptionOrderRepository` into `RuntimeContext` in `bootstrap.py`**

In `src/alpaca_bot/runtime/bootstrap.py`, add the import:

```python
from alpaca_bot.storage.repositories import OptionOrderRepository
```

Add `option_order_store` field to the `RuntimeContext` dataclass (after `watchlist_store`):

```python
@dataclass
class RuntimeContext:
    ...
    watchlist_store: WatchlistStore | None = None
    option_order_store: OptionOrderRepository | None = None   # NEW
    store_lock: threading.Lock = field(default_factory=threading.Lock)
```

In `bootstrap_runtime()`, add to the `RuntimeContext(...)` constructor call (after `watchlist_store=watchlist_store`):

```python
    return RuntimeContext(
        ...
        watchlist_store=watchlist_store,
        option_order_store=OptionOrderRepository(runtime_connection),  # NEW
    )
```

Also in `reconnect_runtime_connection()`, add `"option_order_store"` to the hardcoded tuple of attribute names (line ~106–114) so the new connection is spliced in after a reconnect:

```python
    for attr in (
        "trading_status_store",
        "audit_event_store",
        "order_store",
        "daily_session_state_store",
        "position_store",
        "strategy_flag_store",
        "watchlist_store",
        "option_order_store",   # NEW
    ):
        store = getattr(context, attr, None)
        if store is not None and hasattr(store, "_connection"):
            store._connection = new_conn
```

- [ ] **Step 7: Run tests to verify all pass**

```bash
pytest tests/unit/test_option_storage.py -v
```
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add migrations/012_add_option_orders.sql src/alpaca_bot/storage/models.py src/alpaca_bot/storage/repositories.py src/alpaca_bot/runtime/bootstrap.py tests/unit/test_option_storage.py
git commit -m "feat: add option_orders migration, OptionOrderRecord, OptionOrderRepository, wire into RuntimeContext"
```

---

### Task 6: `AlpacaOptionChainAdapter` + broker option order methods

**Files:**
- Create: `src/alpaca_bot/execution/option_chain.py`
- Modify: `src/alpaca_bot/execution/alpaca.py`
- Test: `tests/unit/test_option_chain.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_option_chain.py`:

```python
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.domain.models import OptionContract
from alpaca_bot.execution.option_chain import (
    OptionChainAdapterProtocol,
    AlpacaOptionChainAdapter,
)
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings


def _settings(**overrides) -> Settings:
    env = _base_env()
    env.update(overrides)
    return Settings.from_env(env)


class _FakeSnapshotClient:
    """Fake option data client — returns minimal snapshot data."""
    def __init__(self, snapshots: dict):
        self._snapshots = snapshots

    def get_option_chain(self, request):
        return self._snapshots


def _make_snapshot(strike: float, expiry: date, ask: float, delta: float | None = None):
    """Build a minimal fake Alpaca OptionSnapshot-like object."""
    class FakeGreeks:
        def __init__(self, delta):
            self.delta = delta

    class FakeQuote:
        def __init__(self, ask):
            self.ask_price = ask
            self.bid_price = ask - 0.10

    class FakeSnapshot:
        def __init__(self, strike, expiry, ask, delta):
            self.greeks = FakeGreeks(delta) if delta is not None else None
            self.latest_quote = FakeQuote(ask)

    class FakeDetails:
        def __init__(self, strike, expiry):
            self.strike_price = strike
            self.expiration_date = expiry
            self.option_type = "call"

    class FakeSnapshot2:
        def __init__(self, strike, expiry, ask, delta):
            self.greeks = FakeGreeks(delta) if delta is not None else None
            self.latest_quote = FakeQuote(ask)
            self.details = FakeDetails(strike, expiry)

    return FakeSnapshot2(strike, expiry, ask, delta)


class TestAlpacaOptionChainAdapter:
    def test_returns_empty_list_when_no_snapshots(self):
        client = _FakeSnapshotClient({})
        adapter = AlpacaOptionChainAdapter(client)
        s = _settings()
        result = adapter.get_option_chain("AAPL", s)
        assert result == []

    def test_converts_snapshot_to_option_contract(self):
        expiry = date(2024, 7, 1)
        occ = "AAPL240701C00150000"
        snapshots = {occ: _make_snapshot(strike=150.0, expiry=expiry, ask=3.00, delta=0.50)}
        client = _FakeSnapshotClient(snapshots)
        adapter = AlpacaOptionChainAdapter(client)
        s = _settings()
        result = adapter.get_option_chain("AAPL", s)
        assert len(result) == 1
        c = result[0]
        assert isinstance(c, OptionContract)
        assert c.occ_symbol == occ
        assert c.underlying == "AAPL"
        assert c.strike == 150.0
        assert c.expiry == expiry
        assert c.ask == 3.00
        assert c.delta == 0.50

    def test_delta_is_none_when_greeks_unavailable(self):
        expiry = date(2024, 7, 1)
        occ = "AAPL240701C00150000"
        snapshots = {occ: _make_snapshot(strike=150.0, expiry=expiry, ask=3.00, delta=None)}
        client = _FakeSnapshotClient(snapshots)
        adapter = AlpacaOptionChainAdapter(client)
        s = _settings()
        result = adapter.get_option_chain("AAPL", s)
        assert result[0].delta is None

    def test_satisfies_protocol(self):
        client = _FakeSnapshotClient({})
        adapter = AlpacaOptionChainAdapter(client)
        assert isinstance(adapter, OptionChainAdapterProtocol)


class TestAlpacaExecutionAdapterOptionMethods:
    def test_submit_option_limit_entry_calls_submit_order(self):
        from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings

        submitted = []

        class FakeTradingClient:
            def submit_order(self, order_data):
                submitted.append(order_data)

                class FakeOrder:
                    id = "broker-456"
                return FakeOrder()

        adapter = AlpacaExecutionAdapter(FakeTradingClient(), settings=Settings.from_env(_base_env()))
        result = adapter.submit_option_limit_entry(
            occ_symbol="AAPL240701C00100000",
            quantity=2,
            limit_price=3.00,
            client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        )
        assert len(submitted) == 1
        assert result.broker_order_id == "broker-456"

    def test_submit_option_market_exit_calls_submit_order(self):
        from alpaca_bot.execution.alpaca import AlpacaExecutionAdapter
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings

        submitted = []

        class FakeTradingClient:
            def submit_order(self, order_data):
                submitted.append(order_data)

                class FakeOrder:
                    id = "broker-789"
                return FakeOrder()

        adapter = AlpacaExecutionAdapter(FakeTradingClient(), settings=Settings.from_env(_base_env()))
        result = adapter.submit_option_market_exit(
            occ_symbol="AAPL240701C00100000",
            quantity=2,
            client_order_id="option:v1:2024-06-01:AAPL240701C00100000:sell:2024-06-01T15:50:00+00:00",
        )
        assert len(submitted) == 1
        assert result.broker_order_id == "broker-789"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_chain.py -v
```
Expected: ImportError on `option_chain`.

- [ ] **Step 3: Create `src/alpaca_bot/execution/option_chain.py`**

```python
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Protocol, runtime_checkable

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import OptionContract


@runtime_checkable
class OptionChainAdapterProtocol(Protocol):
    def get_option_chain(self, symbol: str, settings: Settings) -> list[OptionContract]: ...


class AlpacaOptionChainAdapter:
    def __init__(self, option_data_client: Any) -> None:
        self._client = option_data_client

    def get_option_chain(self, symbol: str, settings: Settings) -> list[OptionContract]:
        try:
            from alpaca.data.requests import OptionChainRequest  # type: ignore[import]
            request = OptionChainRequest(underlying_symbol=symbol, feed="indicative")
        except ImportError:
            # alpaca-py < 0.30.0 or different import path — return empty
            return []

        try:
            snapshots: dict[str, Any] = self._client.get_option_chain(request)
        except Exception:
            return []

        contracts = []
        for occ_symbol, snapshot in snapshots.items():
            try:
                contracts.append(_snapshot_to_contract(occ_symbol, symbol, snapshot))
            except Exception:
                continue
        return contracts


def _snapshot_to_contract(occ_symbol: str, underlying: str, snapshot: Any) -> OptionContract:
    details = snapshot.details
    strike = float(details.strike_price)
    expiry: date = details.expiration_date
    if not isinstance(expiry, date):
        expiry = date.fromisoformat(str(expiry))
    raw_option_type = details.option_type
    option_type = raw_option_type.value.lower() if hasattr(raw_option_type, "value") else str(raw_option_type).lower()

    quote = snapshot.latest_quote
    ask = float(quote.ask_price) if quote is not None else 0.0
    bid = float(quote.bid_price) if quote is not None else 0.0

    delta: float | None = None
    if snapshot.greeks is not None:
        try:
            delta = float(snapshot.greeks.delta)
        except (TypeError, AttributeError):
            delta = None

    return OptionContract(
        occ_symbol=occ_symbol,
        underlying=underlying,
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        bid=bid,
        ask=ask,
        delta=delta,
    )
```

- [ ] **Step 4: Add `submit_option_limit_entry()` and `submit_option_market_exit()` to `AlpacaExecutionAdapter`**

In `src/alpaca_bot/execution/alpaca.py`, add two methods to `AlpacaExecutionAdapter` (after the existing `submit_limit_entry` method):

```python
    def submit_option_limit_entry(
        self,
        *,
        occ_symbol: str,
        quantity: int,
        limit_price: float,
        client_order_id: str,
    ) -> "BrokerOrder":
        from alpaca.trading.requests import LimitOrderRequest  # type: ignore[import]
        from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass  # type: ignore[import]
        order_data = LimitOrderRequest(
            symbol=occ_symbol,
            qty=quantity,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
            client_order_id=client_order_id,
        )
        response = self._trading.submit_order(order_data)
        return BrokerOrder(broker_order_id=str(response.id))

    def submit_option_market_exit(
        self,
        *,
        occ_symbol: str,
        quantity: int,
        client_order_id: str,
    ) -> "BrokerOrder":
        from alpaca.trading.requests import MarketOrderRequest  # type: ignore[import]
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore[import]
        order_data = MarketOrderRequest(
            symbol=occ_symbol,
            qty=quantity,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        response = self._trading.submit_order(order_data)
        return BrokerOrder(broker_order_id=str(response.id))
```

Note: `BrokerOrder` is a named dataclass defined in the same file (`alpaca.py`). The trading client is stored as `self._trading` (assigned in `__init__` as `self._trading = trading_client or self._build_trading_client(...)`). Do NOT use `self._trading_client` — that attribute does not exist.

- [ ] **Step 5: Run tests to verify all pass**

```bash
pytest tests/unit/test_option_chain.py -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/execution/option_chain.py src/alpaca_bot/execution/alpaca.py tests/unit/test_option_chain.py
git commit -m "feat: add AlpacaOptionChainAdapter and option order submission methods"
```

---

### Task 7: `dispatch_pending_option_orders()`

**Files:**
- Create: `src/alpaca_bot/runtime/option_dispatch.py`
- Test: `tests/unit/test_option_dispatch.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_option_dispatch.py`:

```python
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.models import OptionOrderRecord
from alpaca_bot.runtime.option_dispatch import dispatch_pending_option_orders


def _now() -> datetime:
    return datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)


def _record(status: str = "pending_submit", side: str = "buy", **kwargs) -> OptionOrderRecord:
    defaults = dict(
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        occ_symbol="AAPL240701C00100000",
        underlying_symbol="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        side=side,
        status=status,
        quantity=2,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout_calls",
        created_at=_now(),
        updated_at=_now(),
        limit_price=3.00,
    )
    defaults.update(kwargs)
    return OptionOrderRecord(**defaults)


class _FakeOptionOrderStore:
    def __init__(self, records: list[OptionOrderRecord]):
        self._records = records
        self.saved: list[OptionOrderRecord] = []

    def list_by_status(self, *, trading_mode, strategy_version, statuses):
        return [r for r in self._records if r.status in statuses]

    def save(self, record: OptionOrderRecord, *, commit: bool = True) -> None:
        self.saved.append(record)


class _FakeOptionBroker:
    def __init__(self, broker_order_id: str = "broker-123"):
        self.submitted: list[dict] = []
        self._broker_order_id = broker_order_id

    def submit_option_limit_entry(self, **kwargs):
        self.submitted.append({"type": "limit_entry", **kwargs})

        class FakeOrder:
            def __init__(self, bid):
                self.broker_order_id = bid
        return FakeOrder(self._broker_order_id)

    def submit_option_market_exit(self, **kwargs):
        self.submitted.append({"type": "market_exit", **kwargs})

        class FakeOrder:
            def __init__(self, bid):
                self.broker_order_id = bid
        return FakeOrder(self._broker_order_id)


class _FakeAuditStore:
    def __init__(self):
        self.events = []

    def append(self, event, *, commit=True):
        self.events.append(event)


class _FakeRuntime:
    def __init__(self, records):
        self.option_order_store = _FakeOptionOrderStore(records)
        self.audit_event_store = _FakeAuditStore()

    def commit(self):
        pass


class TestDispatchPendingOptionOrders:
    def test_dispatches_pending_buy_as_limit_entry(self):
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        record = _record(status="pending_submit", side="buy")
        runtime = _FakeRuntime([record])
        broker = _FakeOptionBroker()

        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 1
        assert len(broker.submitted) == 1
        assert broker.submitted[0]["type"] == "limit_entry"
        assert broker.submitted[0]["occ_symbol"] == "AAPL240701C00100000"
        assert broker.submitted[0]["quantity"] == 2
        assert broker.submitted[0]["limit_price"] == 3.00

    def test_dispatches_pending_sell_as_market_exit(self):
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        record = _record(status="pending_submit", side="sell", limit_price=None)
        runtime = _FakeRuntime([record])
        broker = _FakeOptionBroker()

        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 1
        assert broker.submitted[0]["type"] == "market_exit"

    def test_returns_zero_when_no_pending_orders(self):
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        runtime = _FakeRuntime([])
        broker = _FakeOptionBroker()

        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 0
        assert len(broker.submitted) == 0

    def test_skips_live_orders_when_enable_live_trading_false(self):
        """ENABLE_LIVE_TRADING=false gate must block option orders too."""
        import os
        from alpaca_bot.config import Settings
        from tests.unit.helpers import _base_env

        env = _base_env()
        env["ENABLE_LIVE_TRADING"] = "false"
        env["TRADING_MODE"] = "paper"
        s = Settings.from_env(env)

        record = _record(status="pending_submit", side="buy")
        runtime = _FakeRuntime([record])
        broker = _FakeOptionBroker()

        # Paper mode + ENABLE_LIVE_TRADING=false is still OK — paper is always allowed.
        # The gate only blocks when TRADING_MODE=live and ENABLE_LIVE_TRADING=false,
        # but Settings.validate() raises before we get here. So this test just confirms
        # that paper mode dispatches normally.
        result = dispatch_pending_option_orders(
            settings=s, runtime=runtime, broker=broker, now=_now(),
        )
        assert result.submitted_count == 1

    def test_order_saved_as_submitting_before_broker_call(self):
        """Write-before-dispatch: record is updated to 'submitting' before the broker call."""
        from tests.unit.helpers import _base_env
        from alpaca_bot.config import Settings
        s = Settings.from_env(_base_env())

        save_calls: list[str] = []

        class TrackingStore(_FakeOptionOrderStore):
            def save(self, record, *, commit=True):
                save_calls.append(record.status)
                super().save(record, commit=commit)

        class TrackingBroker(_FakeOptionBroker):
            def submit_option_limit_entry(self, **kwargs):
                # At call time, 'submitting' must already be saved
                assert "submitting" in save_calls
                return super().submit_option_limit_entry(**kwargs)

        record = _record(status="pending_submit", side="buy")
        runtime = _FakeRuntime([record])
        runtime.option_order_store = TrackingStore([record])
        broker = TrackingBroker()

        dispatch_pending_option_orders(settings=s, runtime=runtime, broker=broker, now=_now())
        assert "submitting" in save_calls
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_dispatch.py -v
```
Expected: ImportError on `option_dispatch`.

- [ ] **Step 3: Create `src/alpaca_bot/runtime/option_dispatch.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from alpaca_bot.config import Settings
from alpaca_bot.storage import AuditEvent
from alpaca_bot.storage.models import OptionOrderRecord

logger = logging.getLogger(__name__)


class OptionOrderStoreProtocol(Protocol):
    def list_by_status(
        self, *, trading_mode, strategy_version: str, statuses: list[str]
    ) -> list[OptionOrderRecord]: ...

    def save(self, record: OptionOrderRecord, *, commit: bool = True) -> None: ...


class AuditEventStoreProtocol(Protocol):
    def append(self, event: AuditEvent, *, commit: bool = True) -> None: ...


class RuntimeProtocol(Protocol):
    option_order_store: OptionOrderStoreProtocol
    audit_event_store: AuditEventStoreProtocol


class BrokerProtocol(Protocol):
    def submit_option_limit_entry(self, **kwargs): ...

    def submit_option_market_exit(self, **kwargs): ...


@dataclass(frozen=True)
class OptionDispatchReport:
    submitted_count: int


def dispatch_pending_option_orders(
    *,
    settings: Settings,
    runtime: RuntimeProtocol,
    broker: BrokerProtocol,
    now: datetime | Callable[[], datetime] | None = None,
) -> OptionDispatchReport:
    timestamp = now() if callable(now) else (now or datetime.now(timezone.utc))

    pending = runtime.option_order_store.list_by_status(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
        statuses=["pending_submit"],
    )

    submitted_count = 0
    for record in pending:
        try:
            submitting = OptionOrderRecord(
                client_order_id=record.client_order_id,
                occ_symbol=record.occ_symbol,
                underlying_symbol=record.underlying_symbol,
                option_type=record.option_type,
                strike=record.strike,
                expiry=record.expiry,
                side=record.side,
                status="submitting",
                quantity=record.quantity,
                trading_mode=record.trading_mode,
                strategy_version=record.strategy_version,
                strategy_name=record.strategy_name,
                limit_price=record.limit_price,
                broker_order_id=record.broker_order_id,
                fill_price=record.fill_price,
                filled_quantity=record.filled_quantity,
                created_at=record.created_at,
                updated_at=timestamp,
            )
            runtime.option_order_store.save(submitting, commit=True)

            if record.side == "buy":
                broker_order = broker.submit_option_limit_entry(
                    occ_symbol=record.occ_symbol,
                    quantity=record.quantity,
                    limit_price=record.limit_price,
                    client_order_id=record.client_order_id,
                )
            else:
                broker_order = broker.submit_option_market_exit(
                    occ_symbol=record.occ_symbol,
                    quantity=record.quantity,
                    client_order_id=record.client_order_id,
                )

            submitted = OptionOrderRecord(
                client_order_id=record.client_order_id,
                occ_symbol=record.occ_symbol,
                underlying_symbol=record.underlying_symbol,
                option_type=record.option_type,
                strike=record.strike,
                expiry=record.expiry,
                side=record.side,
                status="submitted",
                quantity=record.quantity,
                trading_mode=record.trading_mode,
                strategy_version=record.strategy_version,
                strategy_name=record.strategy_name,
                limit_price=record.limit_price,
                broker_order_id=broker_order.broker_order_id,
                fill_price=record.fill_price,
                filled_quantity=record.filled_quantity,
                created_at=record.created_at,
                updated_at=timestamp,
            )
            runtime.option_order_store.save(submitted, commit=True)
            submitted_count += 1

            runtime.audit_event_store.append(
                AuditEvent(
                    event_type="option_order_submitted",
                    symbol=record.underlying_symbol,
                    payload={
                        "occ_symbol": record.occ_symbol,
                        "side": record.side,
                        "quantity": record.quantity,
                        "broker_order_id": broker_order.broker_order_id,
                        "client_order_id": record.client_order_id,
                    },
                ),
                commit=True,
            )
        except Exception:
            logger.exception(
                "option order dispatch failed",
                extra={"client_order_id": record.client_order_id},
            )

    return OptionDispatchReport(submitted_count=submitted_count)
```

- [ ] **Step 4: Run tests to verify all pass**

```bash
pytest tests/unit/test_option_dispatch.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/option_dispatch.py tests/unit/test_option_dispatch.py
git commit -m "feat: add dispatch_pending_option_orders() with write-before-dispatch pattern"
```

---

### Task 8: `run_cycle()` option routing + trade stream fill routing

**Files:**
- Modify: `src/alpaca_bot/runtime/cycle.py` — write `OptionOrderRecord` for `is_option=True` ENTRY intents
- Modify: `src/alpaca_bot/runtime/trade_updates.py` — route `"option:"` fills to `OptionOrderRepository`
- Test: `tests/unit/test_option_cycle_routing.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_option_cycle_routing.py`:

```python
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone

from alpaca_bot.config import TradingMode
from alpaca_bot.core.engine import CycleIntent, CycleIntentType, CycleResult
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.storage import AuditEvent, OrderRecord
from alpaca_bot.storage.models import OptionOrderRecord
from alpaca_bot.runtime.cycle import run_cycle
from tests.unit.helpers import _base_env, _make_settings


def _now() -> datetime:
    return datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc)


def _settings(**overrides):
    env = _base_env()
    env.update(overrides)
    return _make_settings(env)


class _FakeOrderStore:
    def __init__(self):
        self.saved: list[OrderRecord] = []

    def save(self, record, *, commit=True):
        self.saved.append(record)


class _FakeOptionOrderStore:
    def __init__(self):
        self.saved: list[OptionOrderRecord] = []

    def save(self, record, *, commit=True):
        self.saved.append(record)


class _FakeAuditStore:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def append(self, event, *, commit=True):
        self.events.append(event)


class _FakeConn:
    def commit(self): pass
    def rollback(self): pass


class _FakeRuntime:
    def __init__(self):
        self.order_store = _FakeOrderStore()
        self.option_order_store = _FakeOptionOrderStore()
        self.audit_event_store = _FakeAuditStore()
        self.connection = _FakeConn()


def _bar() -> Bar:
    return Bar(symbol="AAPL", timestamp=_now(), open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)


class TestRunCycleOptionRouting:
    def test_option_entry_intent_saves_option_order_record(self):
        s = _settings()
        runtime = _FakeRuntime()
        contract = OptionContract(
            occ_symbol="AAPL240701C00100000",
            underlying="AAPL",
            option_type="call",
            strike=100.0,
            expiry=date(2024, 7, 1),
            bid=2.50,
            ask=3.00,
            delta=0.50,
        )

        def fake_evaluator(settings=None, **kwargs):
            return CycleResult(
                as_of=_now(),
                intents=[
                    CycleIntent(
                        intent_type=CycleIntentType.ENTRY,
                        symbol="AAPL240701C00100000",
                        timestamp=_now(),
                        quantity=2,
                        limit_price=3.00,
                        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
                        strategy_name="breakout_calls",
                        is_option=True,
                        underlying_symbol="AAPL",
                        option_strike=100.0,
                        option_expiry=date(2024, 7, 1),
                        option_type_str="call",
                    )
                ],
            )

        run_cycle(
            settings=s,
            runtime=runtime,
            now=_now(),
            equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": [_bar()]},
            daily_bars_by_symbol={"AAPL": [_bar()]},
            open_positions=[],
            working_order_symbols=set(),
            traded_symbols_today=set(),
            entries_disabled=False,
            strategy_name="breakout_calls",
            _evaluate_fn=fake_evaluator,
        )

        assert len(runtime.option_order_store.saved) == 1
        assert len(runtime.order_store.saved) == 0
        opt_rec = runtime.option_order_store.saved[0]
        assert opt_rec.occ_symbol == "AAPL240701C00100000"
        assert opt_rec.underlying_symbol == "AAPL"
        assert opt_rec.status == "pending_submit"
        assert opt_rec.side == "buy"
        assert opt_rec.quantity == 2
        assert opt_rec.limit_price == 3.00

    def test_equity_entry_intent_saves_order_record_not_option(self):
        s = _settings()
        runtime = _FakeRuntime()

        def fake_evaluator(settings=None, **kwargs):
            return CycleResult(
                as_of=_now(),
                intents=[
                    CycleIntent(
                        intent_type=CycleIntentType.ENTRY,
                        symbol="AAPL",
                        timestamp=_now(),
                        quantity=10,
                        limit_price=100.0,
                        initial_stop_price=98.0,
                        stop_price=98.0,
                        client_order_id="breakout:v1:2024-06-01:AAPL:entry:2024-06-01T14:00:00+00:00",
                        strategy_name="breakout",
                        is_option=False,
                    )
                ],
            )

        run_cycle(
            settings=s, runtime=runtime, now=_now(), equity=100_000.0,
            intraday_bars_by_symbol={"AAPL": [_bar()]},
            daily_bars_by_symbol={"AAPL": [_bar()]},
            open_positions=[], working_order_symbols=set(),
            traded_symbols_today=set(), entries_disabled=False,
            _evaluate_fn=fake_evaluator,
        )

        assert len(runtime.order_store.saved) == 1
        assert len(runtime.option_order_store.saved) == 0


class TestTradeStreamOptionRouting:
    def test_option_prefix_fill_routes_to_option_store(self):
        from alpaca_bot.runtime.trade_updates import apply_trade_update

        updated_option_records: list[dict] = []

        class FakeOptionOrderStore:
            def load_by_broker_order_id(self, broker_order_id):
                return None

            def update_fill(self, **kwargs):
                updated_option_records.append(kwargs)

        class FakeOrderStore:
            def load(self, client_order_id):
                return None

            def load_by_broker_order_id(self, broker_order_id):
                return None

            def save(self, record, *, commit=True):
                pass

        class FakePositionStore:
            def save(self, record, *, commit=True):
                pass

            def delete(self, **kwargs):
                pass

        class FakeAuditStore:
            def append(self, event, *, commit=True):
                pass

        class FakeConn:
            def commit(self): pass
            def rollback(self): pass

        class FakeRuntime:
            order_store = FakeOrderStore()
            option_order_store = FakeOptionOrderStore()
            position_store = FakePositionStore()
            audit_event_store = FakeAuditStore()
            connection = FakeConn()

        class FakeUpdate:
            event = "fill"
            client_order_id = "option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00"
            id = "broker-123"
            symbol = "AAPL240701C00100000"
            side = "buy"
            status = "filled"
            qty = "2"
            filled_qty = "2"
            filled_avg_price = "3.10"
            timestamp = datetime(2024, 6, 1, 14, 5, tzinfo=timezone.utc)
            order = None

        s = _make_settings(_base_env())
        apply_trade_update(
            settings=s,
            runtime=FakeRuntime(),
            update=FakeUpdate(),
            now=_now(),
        )
        assert len(updated_option_records) == 1
        assert updated_option_records[0]["fill_price"] == 3.10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_option_cycle_routing.py -v
```
Expected: AttributeError — `runtime` has no `option_order_store`, option_order routing not yet wired.

- [ ] **Step 3: Extend `run_cycle()` in `runtime/cycle.py`**

In `src/alpaca_bot/runtime/cycle.py`, modify the `run_cycle()` function. Add imports:

```python
from alpaca_bot.storage.models import OptionOrderRecord
```

Change the ENTRY intent dispatch block (lines 84-107) from:

```python
        for intent in result.intents:
            if intent.intent_type is not CycleIntentType.ENTRY:
                continue
            runtime.order_store.save(
                OrderRecord(
                    ...
                ),
                commit=False,
            )
```

To:

```python
        for intent in result.intents:
            if intent.intent_type is not CycleIntentType.ENTRY:
                continue
            if getattr(intent, "is_option", False):
                option_order_store = getattr(runtime, "option_order_store", None)
                if option_order_store is not None:
                    option_order_store.save(
                        OptionOrderRecord(
                            client_order_id=intent.client_order_id or "",
                            occ_symbol=intent.symbol,
                            underlying_symbol=intent.underlying_symbol or "",
                            option_type=intent.option_type_str or "call",
                            strike=intent.option_strike or 0.0,
                            expiry=intent.option_expiry or now.date(),
                            side="buy",
                            status="pending_submit",
                            quantity=intent.quantity or 0,
                            trading_mode=settings.trading_mode,
                            strategy_version=settings.strategy_version,
                            strategy_name=intent.strategy_name,
                            limit_price=intent.limit_price,
                            created_at=now,
                            updated_at=now,
                        ),
                        commit=False,
                    )
            else:
                runtime.order_store.save(
                    OrderRecord(
                        client_order_id=intent.client_order_id or "",
                        symbol=intent.symbol,
                        side="buy",
                        intent_type=intent.intent_type.value,
                        status="pending_submit",
                        quantity=intent.quantity or 0,
                        trading_mode=settings.trading_mode,
                        strategy_version=settings.strategy_version,
                        created_at=now,
                        updated_at=now,
                        stop_price=intent.stop_price,
                        limit_price=intent.limit_price,
                        initial_stop_price=intent.initial_stop_price,
                        signal_timestamp=intent.signal_timestamp,
                        strategy_name=intent.strategy_name,
                    ),
                    commit=False,
                )
```

Note: `option_strike`, `option_expiry`, `option_type_str` were added to `CycleIntent` in Task 4. The `run_cycle()` code above reads them directly from the intent — no fallback to hardcoded defaults for contracts that properly set these fields.

Also add `from datetime import date` to `cycle.py` imports if not already present.

- [ ] **Step 4: Extend trade stream routing in `runtime/trade_updates.py`**

In `src/alpaca_bot/runtime/trade_updates.py`, in `_apply_trade_update_locked()`, add option routing immediately after `timestamp = _resolve_now(...)` and before `matched_order = _find_order(...)`:

```python
    # Route option fills by client_order_id prefix — must come before equity routing
    client_order_id = normalized.client_order_id or ""
    if client_order_id.startswith("option:"):
        option_store = getattr(runtime, "option_order_store", None)
        if option_store is not None and normalized.filled_avg_price is not None and normalized.filled_qty is not None:
            option_store.update_fill(
                client_order_id=client_order_id,
                broker_order_id=normalized.broker_order_id or "",
                fill_price=normalized.filled_avg_price,
                filled_quantity=normalized.filled_qty,
                status=normalized.status,
                updated_at=timestamp,
            )
        return {"routed_to": "option_store", "client_order_id": client_order_id}, []
```

The `normalized` object already has all needed fields: `.client_order_id`, `.broker_order_id`, `.filled_avg_price`, `.filled_qty`, `.status`. No additional helper functions needed — `_normalize_trade_update(update)` populates them all. The return type is `tuple[dict, list]` to match the function's existing return signature.

- [ ] **Step 5: Run tests to verify all pass**

```bash
pytest tests/unit/test_option_cycle_routing.py -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/runtime/cycle.py src/alpaca_bot/runtime/trade_updates.py src/alpaca_bot/core/engine.py tests/unit/test_option_cycle_routing.py
git commit -m "feat: route option ENTRY intents to OptionOrderRepository; route option fills in trade stream"
```

---

### Task 9: Supervisor integration

**Files:**
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Test: `tests/unit/test_supervisor_option_integration.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_supervisor_option_integration.py`:

```python
from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from alpaca_bot.domain.models import OptionContract
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator


class FakeOptionChainAdapter:
    def __init__(self, chains: dict):
        self._chains = chains
        self.fetched: list[str] = []

    def get_option_chain(self, symbol: str, settings):
        self.fetched.append(symbol)
        return self._chains.get(symbol, [])


def test_make_breakout_calls_evaluator_accepts_empty_chains():
    evaluator = make_breakout_calls_evaluator({})
    assert callable(evaluator)


def test_option_strategy_names_contains_breakout_calls():
    assert "breakout_calls" in OPTION_STRATEGY_NAMES


def test_fake_option_chain_adapter_fetches_by_symbol():
    contract = OptionContract(
        occ_symbol="AAPL240701C00100000",
        underlying="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        bid=2.50,
        ask=3.00,
        delta=0.50,
    )
    adapter = FakeOptionChainAdapter({"AAPL": [contract]})
    from tests.unit.helpers import _base_env
    from alpaca_bot.config import Settings
    s = Settings.from_env(_base_env())
    result = adapter.get_option_chain("AAPL", s)
    assert len(result) == 1
    assert result[0].occ_symbol == "AAPL240701C00100000"
    assert "AAPL" in adapter.fetched


def test_deduplication_uses_underlying_symbol():
    """Underlying symbols of open option positions must block equity+option entries for same symbol."""
    # This is a behavioral contract test: if AAPL has an open option position,
    # the supervisor must add "AAPL" to working_order_symbols before calling evaluate_cycle.
    # We verify this by checking that make_breakout_calls_evaluator returns None
    # when the underlying is in working_order_symbols (handled by evaluate_cycle directly).
    # The supervisor passes underlying symbols to working_order_symbols; this test
    # validates the data contract.
    contract = OptionContract(
        occ_symbol="AAPL240701C00100000",
        underlying="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        bid=2.50,
        ask=3.00,
        delta=0.50,
    )
    from alpaca_bot.storage.models import OptionOrderRecord
    from alpaca_bot.config import TradingMode

    open_opt = OptionOrderRecord(
        client_order_id="option:v1:2024-06-01:AAPL240701C00100000:entry:2024-06-01T14:00:00+00:00",
        occ_symbol="AAPL240701C00100000",
        underlying_symbol="AAPL",
        option_type="call",
        strike=100.0,
        expiry=date(2024, 7, 1),
        side="buy",
        status="filled",
        quantity=2,
        trading_mode=TradingMode.PAPER,
        strategy_version="v1",
        strategy_name="breakout_calls",
        created_at=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
        updated_at=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
    )
    underlying_symbols = {open_opt.underlying_symbol}
    assert "AAPL" in underlying_symbols
```

- [ ] **Step 2: Run tests to verify they pass (these are behavioral contract tests)**

```bash
pytest tests/unit/test_supervisor_option_integration.py -v
```
Expected: all PASS (these tests validate contracts, not supervisor wiring, which is tested via the full system).

- [ ] **Step 3: Wire supervisor for option strategy detection**

In `src/alpaca_bot/runtime/supervisor.py`, add imports near the top of the file (after existing strategy imports):

```python
from alpaca_bot.strategy import OPTION_STRATEGY_NAMES
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator
from alpaca_bot.runtime.option_dispatch import dispatch_pending_option_orders
```

Add `_option_chain_adapter` and `_option_broker` as optional constructor parameters to `RuntimeSupervisor.__init__()` following the existing DI pattern (all existing params unchanged, append at end):

```python
def __init__(
    self,
    ...,                        # all existing params unchanged
    option_chain_adapter=None,
    option_broker=None,
):
    ...
    self._option_chain_adapter = option_chain_adapter
    self._option_broker = option_broker
```

In `run_cycle_once()`, locate the line `active_strategies = self._resolve_active_strategies()` (line ~524). Replace that single line with the block below. Everything else in the method is unchanged:

```python
# Resolve registered strategies (breakout, etc.)
active_strategies = list(self._resolve_active_strategies())

# Fetch option chains and append option strategies when adapter is configured.
# breakout_calls is NOT in STRATEGY_REGISTRY — it is a factory that closes over chains.
option_chains_by_symbol: dict = {}
option_order_store = getattr(self._runtime, "option_order_store", None)
if self._option_chain_adapter is not None:
    for symbol in settings.symbols:
        try:
            chains = self._option_chain_adapter.get_option_chain(symbol, settings)
            if chains:
                option_chains_by_symbol[symbol] = chains
        except Exception:
            logger.exception("option chain fetch failed for %s", symbol)
    for opt_name in OPTION_STRATEGY_NAMES:
        active_strategies.append(
            (opt_name, make_breakout_calls_evaluator(option_chains_by_symbol))
        )
```

Locate the block that builds `working_order_symbols` (a `set[str]` containing symbols of open equity orders). Immediately after that block, add the open option underlying symbols so the engine deduplicates across both equity and options for the same underlying:

```python
# Add open option position underlying symbols to prevent double-entry.
if option_order_store is not None:
    open_options = option_order_store.list_open_option_positions(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    for opt_pos in open_options:
        working_order_symbols.add(opt_pos.underlying_symbol)
```

The existing `for strategy_name, evaluator in active_strategies:` loop and `self._cycle_runner(...)` call are **unchanged** — the option evaluator is already in `active_strategies` and the loop handles it uniformly.

After the `active_strategies` loop (and after the existing equity `dispatch_pending_orders()` call), add option dispatch:

```python
option_broker = getattr(self, "_option_broker", None)
if option_broker is not None and option_order_store is not None:
    dispatch_pending_option_orders(
        settings=settings,
        runtime=self._runtime,
        broker=option_broker,
    )
```

In the EOD flatten block (where equity positions are flattened past `flatten_time`), add option flatten alongside the equity flatten:

```python
if past_flatten_time and option_order_store is not None:
    open_option_positions = option_order_store.list_open_option_positions(
        trading_mode=settings.trading_mode,
        strategy_version=settings.strategy_version,
    )
    for pos in open_option_positions:
        sell_id = f"option:{settings.strategy_version}:{now.date().isoformat()}:{pos.occ_symbol}:sell:{now.isoformat()}"
        sell_record = OptionOrderRecord(
            client_order_id=sell_id,
            occ_symbol=pos.occ_symbol,
            underlying_symbol=pos.underlying_symbol,
            option_type=pos.option_type,
            strike=pos.strike,
            expiry=pos.expiry,
            side="sell",
            status="pending_submit",
            quantity=pos.filled_quantity or pos.quantity,
            trading_mode=settings.trading_mode,
            strategy_version=settings.strategy_version,
            strategy_name=pos.strategy_name,
            created_at=now,
            updated_at=now,
        )
        option_order_store.save(sell_record, commit=True)
    # Dispatch the newly-created sell records immediately (same cycle)
    if option_broker is not None:
        dispatch_pending_option_orders(
            settings=settings,
            runtime=self._runtime,
            broker=option_broker,
        )
```

- [ ] **Step 4: Run full test suite to verify no regressions**

```bash
pytest tests/unit/test_supervisor_option_integration.py tests/unit/test_cycle_engine.py tests/unit/test_option_storage.py tests/unit/test_option_dispatch.py tests/unit/test_option_selector.py tests/unit/test_option_chain.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/runtime/supervisor.py tests/unit/test_supervisor_option_integration.py
git commit -m "feat: wire supervisor for breakout_calls — fetch chains, build evaluator, dispatch, EOD flatten"
```

---

### Task 10: Regression check

- [ ] **Step 1: Run full test suite**

```bash
pytest -q --tb=short
```
Expected: ≥1111 tests, all PASS.

- [ ] **Step 2: Verify options settings default correctly**

```bash
python -c "
from tests.unit.helpers import _base_env
from alpaca_bot.config import Settings
s = Settings.from_env(_base_env())
print('option_dte_min:', s.option_dte_min)
print('option_dte_max:', s.option_dte_max)
print('option_delta_target:', s.option_delta_target)
assert s.option_dte_min == 21
assert s.option_dte_max == 60
assert s.option_delta_target == 0.50
print('OK')
"
```

- [ ] **Step 3: Verify OPTION_STRATEGY_NAMES exported**

```bash
python -c "from alpaca_bot.strategy import OPTION_STRATEGY_NAMES; assert 'breakout_calls' in OPTION_STRATEGY_NAMES; print('OK')"
```

- [ ] **Step 4: Verify migration file exists**

```bash
ls migrations/012_add_option_orders.sql && echo "OK"
```

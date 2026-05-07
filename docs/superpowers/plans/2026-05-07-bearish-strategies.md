# Bearish Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 11 put option strategies that profit from market downturns, mirroring every existing long strategy with inverted signal logic, plus shared infrastructure (put contract selector, downtrend filter, strategy registry).

**Architecture:** Each bear strategy is a file exporting a signal function and a factory; the factory wraps the signal with `select_put_contract()`. `OPTION_STRATEGY_FACTORIES` replaces the hardcoded `make_breakout_calls_evaluator` in the supervisor dispatch loop. Regime filter is bypassed for all option strategies (passed as `None`).

**Tech Stack:** Python, pytest, SimpleNamespace fakes, existing `Bar`/`OptionContract`/`EntrySignal` domain types.

---

## File Map

| Action | File | Purpose |
|---|---|---|
| Create | `tests/unit/bear_test_helpers.py` | Shared bar/settings/contract fixtures for all bear tests |
| Modify | `src/alpaca_bot/strategy/breakout.py` | Add `daily_downtrend_filter_passes()` |
| Modify | `src/alpaca_bot/strategy/option_selector.py` | Add `select_put_contract()` |
| Create | `tests/unit/test_bear_shared.py` | Tests for downtrend filter + put selector |
| Create | `src/alpaca_bot/strategy/bear_breakdown.py` | Bear breakdown signal + factory |
| Create | `tests/unit/test_bear_breakdown.py` | Tests for bear_breakdown |
| Create | `src/alpaca_bot/strategy/bear_momentum.py` | Bear momentum signal + factory |
| Create | `tests/unit/test_bear_momentum.py` | Tests for bear_momentum |
| Create | `src/alpaca_bot/strategy/bear_orb.py` | Bear ORB signal + factory |
| Create | `tests/unit/test_bear_orb.py` | Tests for bear_orb |
| Create | `src/alpaca_bot/strategy/bear_low_watermark.py` | Bear low watermark signal + factory |
| Create | `tests/unit/test_bear_low_watermark.py` | Tests for bear_low_watermark |
| Create | `src/alpaca_bot/strategy/bear_ema_rejection.py` | Bear EMA rejection signal + factory |
| Create | `tests/unit/test_bear_ema_rejection.py` | Tests for bear_ema_rejection |
| Create | `src/alpaca_bot/strategy/bear_vwap_breakdown.py` | Bear VWAP breakdown signal + factory |
| Create | `tests/unit/test_bear_vwap_breakdown.py` | Tests for bear_vwap_breakdown |
| Create | `src/alpaca_bot/strategy/bear_gap_and_drop.py` | Bear gap-and-drop signal + factory |
| Create | `tests/unit/test_bear_gap_and_drop.py` | Tests for bear_gap_and_drop |
| Create | `src/alpaca_bot/strategy/bear_flag.py` | Bear flag signal + factory |
| Create | `tests/unit/test_bear_flag.py` | Tests for bear_flag |
| Create | `src/alpaca_bot/strategy/bear_vwap_cross_down.py` | Bear VWAP cross down signal + factory |
| Create | `tests/unit/test_bear_vwap_cross_down.py` | Tests for bear_vwap_cross_down |
| Create | `src/alpaca_bot/strategy/bear_bb_squeeze_down.py` | Bear BB squeeze down signal + factory |
| Create | `tests/unit/test_bear_bb_squeeze_down.py` | Tests for bear_bb_squeeze_down |
| Create | `src/alpaca_bot/strategy/bear_failed_breakout.py` | Bear failed breakout signal + factory |
| Create | `tests/unit/test_bear_failed_breakout.py` | Tests for bear_failed_breakout |
| Modify | `src/alpaca_bot/strategy/__init__.py` | Add `OPTION_STRATEGY_FACTORIES`, derive `OPTION_STRATEGY_NAMES` |
| Modify | `src/alpaca_bot/runtime/supervisor.py` | Factory dispatch loop + regime filter bypass |
| Create | `tests/unit/test_bear_registry.py` | Tests for registry completeness + supervisor dispatch |
| Modify | `DEPLOYMENT.md` | Note inverse ETF config (SQQQ, SPXS, SOXS) |

---

### Task 1: Shared test helpers + downtrend filter + put selector

**Files:**
- Create: `tests/unit/bear_test_helpers.py`
- Modify: `src/alpaca_bot/strategy/breakout.py`
- Modify: `src/alpaca_bot/strategy/option_selector.py`
- Create: `tests/unit/test_bear_shared.py`

- [ ] **Step 1: Create shared test helpers**

```python
# tests/unit/bear_test_helpers.py
from datetime import date, datetime, timezone
from types import SimpleNamespace

from alpaca_bot.domain.models import Bar, OptionContract


def _settings(**overrides) -> object:
    base = dict(
        daily_sma_period=5,
        breakout_lookback_bars=3,
        relative_volume_lookback_bars=3,
        relative_volume_threshold=1.5,
        atr_period=3,
        atr_stop_multiplier=1.0,
        entry_stop_price_buffer=0.01,
        ema_period=9,
        high_watermark_lookback_days=5,
        orb_opening_bars=2,
        vwap_dip_threshold_pct=0.015,
        gap_threshold_pct=0.02,
        gap_volume_threshold=2.0,
        bull_flag_min_run_pct=0.02,
        bull_flag_consolidation_volume_ratio=0.6,
        bull_flag_consolidation_range_pct=0.5,
        bb_period=5,
        bb_std_dev=2.0,
        bb_squeeze_threshold_pct=0.03,
        bb_squeeze_min_bars=2,
        failed_breakdown_volume_ratio=2.0,
        failed_breakdown_recapture_buffer_pct=0.001,
        option_dte_min=21,
        option_dte_max=60,
        option_delta_target=0.5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _bar(
    close: float,
    *,
    ts: datetime | None = None,
    volume: float = 1000.0,
    high: float | None = None,
    low: float | None = None,
    open: float | None = None,
) -> Bar:
    ts = ts or datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
    return Bar(
        symbol="AAPL",
        timestamp=ts,
        open=open if open is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
    )


def _downtrend_daily_bars() -> list[Bar]:
    """7 daily bars where prior close (80) is below 5-bar SMA (96) → downtrend True."""
    # window = bars[-6:-1] = bars[1:6], closes=[100,100,100,100,100], sma=100
    # Wait — let's use: closes=[110,110,110,110,110,80,80]
    # window=bars[1:6]=[110,110,110,110,110], sma=110, latest_close=bars[5].close=80, 80<110 → True
    closes = [110, 110, 110, 110, 110, 80, 80]
    bars = []
    for i, c in enumerate(closes):
        d = date(2024, 1, 8 + i)
        ts = datetime(d.year, d.month, d.day, 16, 0, tzinfo=timezone.utc)
        bars.append(
            Bar(symbol="AAPL", timestamp=ts, open=c, high=c + 2, low=c - 2, close=c, volume=1000.0)
        )
    return bars


def _uptrend_daily_bars() -> list[Bar]:
    """7 daily bars where prior close (100) is above 5-bar SMA (82) → downtrend False."""
    # closes=[80,80,80,80,80,100,100]
    # window=bars[1:6]=[80,80,80,80,80], sma=80, latest_close=100, 100<80 → False
    closes = [80, 80, 80, 80, 80, 100, 100]
    bars = []
    for i, c in enumerate(closes):
        d = date(2024, 1, 8 + i)
        ts = datetime(d.year, d.month, d.day, 16, 0, tzinfo=timezone.utc)
        bars.append(
            Bar(symbol="AAPL", timestamp=ts, open=c, high=c + 2, low=c - 2, close=c, volume=1000.0)
        )
    return bars


def _put_contract(*, strike: float = 95.0, delta: float = -0.5) -> OptionContract:
    return OptionContract(
        occ_symbol="AAPL240216P00095000",
        underlying="AAPL",
        option_type="put",
        strike=strike,
        expiry=date(2024, 2, 16),
        bid=2.0,
        ask=2.10,
        delta=delta,
    )
```

- [ ] **Step 2: Add `daily_downtrend_filter_passes()` to `breakout.py`**

Open `src/alpaca_bot/strategy/breakout.py`. After `daily_trend_filter_passes()`, add:

```python
def daily_downtrend_filter_passes(daily_bars: Sequence[Bar], settings: Settings) -> bool:
    """Returns True when the prior close is BELOW the SMA — stock is in a downtrend."""
    if len(daily_bars) < settings.daily_sma_period + 1:
        return False
    window = daily_bars[-settings.daily_sma_period - 1 : -1]
    sma = sum(bar.close for bar in window) / len(window)
    latest_close = window[-1].close
    return latest_close < sma
```

- [ ] **Step 3: Add `select_put_contract()` to `option_selector.py`**

Open `src/alpaca_bot/strategy/option_selector.py`. After `select_call_contract()`, add:

```python
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
    ]
    if not eligible:
        return None
    with_delta = [c for c in eligible if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(abs(c.delta) - settings.option_delta_target))
    return min(eligible, key=lambda c: abs(c.strike - current_price))
```

- [ ] **Step 4: Write tests for shared infrastructure**

```python
# tests/unit/test_bear_shared.py
from datetime import date, timezone, datetime

import pytest

from alpaca_bot.domain.models import OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract
from tests.unit.bear_test_helpers import _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract


class TestDailyDowntrendFilter:
    def test_downtrend_returns_true(self):
        bars = _downtrend_daily_bars()
        assert daily_downtrend_filter_passes(bars, _settings()) is True

    def test_uptrend_returns_false(self):
        bars = _uptrend_daily_bars()
        assert daily_downtrend_filter_passes(bars, _settings()) is False

    def test_insufficient_bars_returns_false(self):
        from tests.unit.bear_test_helpers import _bar
        from datetime import date, datetime, timezone
        bars = [_bar(80.0) for _ in range(3)]
        assert daily_downtrend_filter_passes(bars, _settings()) is False


class TestSelectPutContract:
    def test_selects_put_by_delta(self):
        today = date(2024, 1, 15)
        c1 = _put_contract(strike=95.0, delta=-0.5)
        c2 = _put_contract(strike=90.0, delta=-0.3)
        result = select_put_contract([c1, c2], current_price=100.0, today=today, settings=_settings())
        assert result is c1  # abs(-0.5) closer to target 0.5

    def test_skips_calls(self):
        today = date(2024, 1, 15)
        call = OptionContract(
            occ_symbol="AAPL240216C00095000",
            underlying="AAPL",
            option_type="call",
            strike=95.0,
            expiry=date(2024, 2, 16),
            bid=2.0,
            ask=2.10,
            delta=0.5,
        )
        result = select_put_contract([call], current_price=100.0, today=today, settings=_settings())
        assert result is None

    def test_skips_zero_ask(self):
        today = date(2024, 1, 15)
        c = OptionContract(
            occ_symbol="AAPL240216P00095000",
            underlying="AAPL",
            option_type="put",
            strike=95.0,
            expiry=date(2024, 2, 16),
            bid=0.0,
            ask=0.0,
            delta=-0.5,
        )
        result = select_put_contract([c], current_price=100.0, today=today, settings=_settings())
        assert result is None

    def test_dte_filter(self):
        today = date(2024, 1, 15)
        c_near = OptionContract(
            occ_symbol="AAPL240120P00095000",
            underlying="AAPL",
            option_type="put",
            strike=95.0,
            expiry=date(2024, 1, 20),  # 5 DTE — below min 21
            bid=0.5,
            ask=0.6,
            delta=-0.5,
        )
        result = select_put_contract([c_near], current_price=100.0, today=today, settings=_settings())
        assert result is None

    def test_falls_back_to_strike_proximity_without_delta(self):
        today = date(2024, 1, 15)
        c1 = OptionContract(
            occ_symbol="AAPL240216P00098000",
            underlying="AAPL",
            option_type="put",
            strike=98.0,
            expiry=date(2024, 2, 16),
            bid=2.0,
            ask=2.10,
            delta=None,
        )
        c2 = OptionContract(
            occ_symbol="AAPL240216P00090000",
            underlying="AAPL",
            option_type="put",
            strike=90.0,
            expiry=date(2024, 2, 16),
            bid=1.5,
            ask=1.60,
            delta=None,
        )
        result = select_put_contract([c1, c2], current_price=100.0, today=today, settings=_settings())
        assert result is c1  # 98 closer to 100
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_bear_shared.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/bear_test_helpers.py tests/unit/test_bear_shared.py \
        src/alpaca_bot/strategy/breakout.py \
        src/alpaca_bot/strategy/option_selector.py
git commit -m "feat: add daily_downtrend_filter_passes and select_put_contract; shared bear test helpers"
```

---

### Task 2: bear_breakdown

**Files:**
- Create: `src/alpaca_bot/strategy/bear_breakdown.py`
- Create: `tests/unit/test_bear_breakdown.py`

Mirror of `breakout.py`. Signal fires when `signal_bar.low < min(intraday_bars[i-lookback:i].low)` + relative volume + downtrend filter.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_breakdown.py
import pytest
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_breakdown import evaluate_bear_breakdown_signal, make_bear_breakdown_evaluator


def _intraday(signal_close: float, *, avg_vol: float = 1000.0, signal_vol: float = 2000.0) -> list:
    """5 intraday bars, signal at index 4. Lookback=3 so lookback window=[1,2,3]."""
    prior_low = 100.0
    bars = [
        _bar(102.0, low=prior_low, volume=avg_vol),  # 0
        _bar(101.0, low=prior_low, volume=avg_vol),  # 1
        _bar(100.5, low=prior_low, volume=avg_vol),  # 2
        _bar(100.0, low=prior_low, volume=avg_vol),  # 3
        _bar(signal_close, low=signal_close - 0.5, high=signal_close + 0.5, volume=signal_vol),  # 4 = signal
    ]
    return bars


class TestBearBreakdownSignal:
    def test_breakdown_fires_on_new_low_with_volume_and_downtrend(self):
        bars = _intraday(98.0, signal_vol=2000.0)  # new low vs lookback [1,2,3]
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.symbol == "AAPL"

    def test_no_signal_when_not_new_low(self):
        bars = _intraday(100.5)  # low=100.0, not below lookback low=100
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _intraday(98.0, signal_vol=2000.0)
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_low_volume(self):
        bars = _intraday(98.0, avg_vol=1000.0, signal_vol=500.0)
        result = evaluate_bear_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearBreakdownEvaluator:
    def test_evaluator_returns_entry_signal_with_put(self):
        chains = {"AAPL": [_put_contract()]}
        evaluator = make_bear_breakdown_evaluator(chains)
        bars = _intraday(98.0, signal_vol=2000.0)
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract is not None
        assert result.option_contract.option_type == "put"

    def test_evaluator_returns_none_when_no_chains(self):
        evaluator = make_bear_breakdown_evaluator({})
        bars = _intraday(98.0, signal_vol=2000.0)
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/unit/test_bear_breakdown.py -v
```

Expected: ImportError — module not found.

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_breakdown.py
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_breakdown_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    lookback = settings.breakout_lookback_bars
    if signal_index < lookback:
        return None
    signal_bar = intraday_bars[signal_index]
    window = intraday_bars[signal_index - lookback : signal_index]
    prior_low = min(bar.low for bar in window)
    if signal_bar.low >= prior_low:
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    if avg_vol <= 0 or signal_bar.volume / avg_vol < settings.relative_volume_threshold:
        return None
    # ATR-based stop above breakdown level
    atr_window = intraday_bars[max(0, signal_index - settings.atr_period) : signal_index + 1]
    if len(atr_window) < 2:
        return None
    trs = [max(b.high - b.low, abs(b.high - atr_window[i].close), abs(b.low - atr_window[i].close))
           for i, b in enumerate(atr_window[1:], 1)]
    atr = sum(trs) / len(trs)
    stop_price = signal_bar.low + atr * settings.atr_stop_multiplier
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.low,
        relative_volume=signal_bar.volume / avg_vol,
        stop_price=stop_price,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_breakdown_evaluator(
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
        equity_signal = evaluate_bear_breakdown_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

**Note:** `settings.market_timezone` is a `ZoneInfo` object available on the real `Settings` dataclass. In the factory's `today` lookup line, we call `.astimezone(settings.market_timezone).date()`. Tests don't call the factory's `today` logic with a UTC-aware timestamp — the test bar already has `tzinfo=timezone.utc` and `settings` is a `SimpleNamespace` without `market_timezone`. To avoid this, the test for the factory in `TestMakeBearBreakdownEvaluator` uses a UTC timestamp and the `SimpleNamespace` will raise `AttributeError` at the `.astimezone` call. 

Fix: add `market_timezone` to the `_settings()` helper in `bear_test_helpers.py`:

```python
from zoneinfo import ZoneInfo
# In _settings base dict, add:
market_timezone=ZoneInfo("America/New_York"),
```

- [ ] **Step 4: Update `bear_test_helpers.py` to add `market_timezone`**

Edit `tests/unit/bear_test_helpers.py`:

```python
# At top, add:
from zoneinfo import ZoneInfo

# In _settings base dict, add:
market_timezone=ZoneInfo("America/New_York"),
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/test_bear_breakdown.py tests/unit/test_bear_shared.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/alpaca_bot/strategy/bear_breakdown.py \
        tests/unit/test_bear_breakdown.py \
        tests/unit/bear_test_helpers.py
git commit -m "feat: add bear_breakdown put strategy"
```

---

### Task 3: bear_momentum

**Files:**
- Create: `src/alpaca_bot/strategy/bear_momentum.py`
- Create: `tests/unit/test_bear_momentum.py`

Signal: 3+ consecutive down-bars (each close < prior close) + downtrend filter.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_momentum.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_momentum import evaluate_bear_momentum_signal, make_bear_momentum_evaluator
from datetime import datetime, timezone


def _intraday_down_streak(streak_len: int = 3) -> list:
    """streak_len consecutive down-bars ending at signal_index=streak_len."""
    base = 110.0
    bars = [_bar(base)]  # bar 0 = anchor
    for i in range(streak_len):
        bars.append(_bar(base - (i + 1) * 2.0))
    return bars


class TestBearMomentumSignal:
    def test_fires_on_three_consecutive_down_bars(self):
        bars = _intraday_down_streak(3)
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_on_two_down_bars(self):
        bars = _intraday_down_streak(2)
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _intraday_down_streak(3)
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_streak_broken(self):
        # bar sequence: 110, 108, 109 (up), 107 — streak broken at index 2
        bars = [_bar(110.0), _bar(108.0), _bar(109.0), _bar(107.0)]
        result = evaluate_bear_momentum_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None


class TestMakeBearMomentumEvaluator:
    def test_returns_put_entry_signal(self):
        chains = {"AAPL": [_put_contract()]}
        evaluator = make_bear_momentum_evaluator(chains)
        bars = _intraday_down_streak(3)
        result = evaluator(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=3,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None
        assert result.option_contract.option_type == "put"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_momentum.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_momentum.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract

_MIN_CONSECUTIVE_DOWN_BARS = 3


def evaluate_bear_momentum_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    if signal_index < _MIN_CONSECUTIVE_DOWN_BARS:
        return None
    window = intraday_bars[signal_index - _MIN_CONSECUTIVE_DOWN_BARS : signal_index + 1]
    for i in range(1, len(window)):
        if window[i].close >= window[i - 1].close:
            return None
    signal_bar = intraday_bars[signal_index]
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        avg_vol = sum(b.volume for b in intraday_bars[:signal_index]) / max(signal_index, 1)
    else:
        avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=rel_vol,
        stop_price=signal_bar.high + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_momentum_evaluator(
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
        equity_signal = evaluate_bear_momentum_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_momentum.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_momentum.py tests/unit/test_bear_momentum.py
git commit -m "feat: add bear_momentum put strategy"
```

---

### Task 4: bear_orb

**Files:**
- Create: `src/alpaca_bot/strategy/bear_orb.py`
- Create: `tests/unit/test_bear_orb.py`

Signal: close below opening-range low (first `orb_opening_bars` bars) after opening range has formed + downtrend filter. Volume baseline = average volume of opening range bars.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_orb.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_orb import evaluate_bear_orb_signal, make_bear_orb_evaluator
from datetime import datetime, timezone


def _intraday(signal_close: float, *, opening_low: float = 100.0, signal_vol: float = 2000.0) -> list:
    """2 opening bars (orb_opening_bars=2), then signal bar at index 2."""
    avg_vol = 1000.0
    return [
        _bar(101.0, low=opening_low, high=102.0, volume=avg_vol),  # 0 — opening bar 1
        _bar(100.5, low=opening_low, high=101.5, volume=avg_vol),  # 1 — opening bar 2
        _bar(signal_close, low=signal_close - 0.5, high=signal_close + 0.2, volume=signal_vol),  # 2 signal
    ]


class TestBearOrbSignal:
    def test_fires_below_opening_range_low(self):
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_above_opening_range_low(self):
        bars = _intraday(100.5, opening_low=100.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_before_orb_complete(self):
        bars = _intraday(98.5, opening_low=100.0, signal_vol=2500.0)
        result = evaluate_bear_orb_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=1,  # only 1 opening bar formed, need 2
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_orb.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_orb.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_orb_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    opening_bars_count = settings.orb_opening_bars
    if signal_index < opening_bars_count:
        return None
    opening_bars = intraday_bars[:opening_bars_count]
    opening_range_low = min(bar.low for bar in opening_bars)
    signal_bar = intraday_bars[signal_index]
    if signal_bar.close >= opening_range_low:
        return None
    # Volume baseline = average of opening range bars
    avg_vol = sum(b.volume for b in opening_bars) / len(opening_bars)
    if avg_vol <= 0 or signal_bar.volume / avg_vol < settings.relative_volume_threshold:
        return None
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=signal_bar.volume / avg_vol,
        stop_price=opening_range_low + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_orb_evaluator(
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
        equity_signal = evaluate_bear_orb_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_orb.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_orb.py tests/unit/test_bear_orb.py
git commit -m "feat: add bear_orb put strategy"
```

---

### Task 5: bear_low_watermark

**Files:**
- Create: `src/alpaca_bot/strategy/bear_low_watermark.py`
- Create: `tests/unit/test_bear_low_watermark.py`

Signal: `signal_bar.low < historical_low AND signal_bar.close < historical_low` where `historical_low = min(bar.low for bar in daily_bars[-lookback_days:])` (excluding today's bar via `daily_bars[:-1]`).

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_low_watermark.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_low_watermark import evaluate_bear_low_watermark_signal, make_bear_low_watermark_evaluator


def _intraday_bars(signal_close: float, signal_low: float) -> list:
    return [
        _bar(105.0),
        _bar(104.0),
        _bar(signal_close, low=signal_low),
    ]


class TestBearLowWatermarkSignal:
    def test_fires_on_new_session_low(self):
        # _downtrend_daily_bars has closes [110,110,110,110,110,80,80]
        # lows = closes-2 = [108,108,...,78,78]
        # completed = daily_bars[:-1], lookback=5, historical_low = min of last 5 completed bar lows
        # Last 5 completed bar lows: [108,108,108,108,108] -> historical_low=108
        # Actually: _downtrend_daily_bars bars[-1] is today (bar 6), bars[:-1] ends at bar 5
        # bars[-lookback_days:] = bars[-5:] = bars[1:6], lows = [108,108,108,108,78] = min=78
        # Signal low needs to be < 78
        daily = _downtrend_daily_bars()  # completed = daily[:-1] (6 bars), lookback=5
        signal_low = 75.0  # below 78
        bars = _intraday_bars(75.5, signal_low)
        result = evaluate_bear_low_watermark_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=daily,
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_above_historical_low(self):
        daily = _downtrend_daily_bars()
        bars = _intraday_bars(80.0, 79.0)  # above historical low of 78
        result = evaluate_bear_low_watermark_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=daily,
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        daily = _uptrend_daily_bars()
        bars = _intraday_bars(75.0, 74.0)
        result = evaluate_bear_low_watermark_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=2,
            daily_bars=daily,
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_low_watermark.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_low_watermark.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_low_watermark_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    lookback_days = settings.high_watermark_lookback_days
    completed = daily_bars[:-1]  # exclude today's partial bar
    if len(completed) < lookback_days:
        return None
    historical_bars = completed[-lookback_days:]
    historical_low = min(bar.low for bar in historical_bars)
    signal_bar = intraday_bars[signal_index]
    if signal_bar.low >= historical_low or signal_bar.close >= historical_low:
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.low,
        relative_volume=rel_vol,
        stop_price=historical_low + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_low_watermark_evaluator(
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
        equity_signal = evaluate_bear_low_watermark_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_low_watermark.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_low_watermark.py tests/unit/test_bear_low_watermark.py
git commit -m "feat: add bear_low_watermark put strategy"
```

---

### Task 6: bear_ema_rejection

**Files:**
- Create: `src/alpaca_bot/strategy/bear_ema_rejection.py`
- Create: `tests/unit/test_bear_ema_rejection.py`

Signal: price crosses EMA from above (rejection) — prior close >= EMA, signal close < EMA + downtrend filter. `_calculate_ema` helper is private in `ema_pullback.py`; duplicate verbatim.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_ema_rejection.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_ema_rejection import evaluate_bear_ema_rejection_signal, make_bear_ema_rejection_evaluator


def _intraday_ema_rejection() -> list:
    """15 bars declining. EMA period=9. Bar 13 stays above EMA, bar 14 crosses below."""
    # Start at 110, decline slowly so EMA lags above close
    bars = []
    for i in range(15):
        close = 110.0 - i * 0.3
        bars.append(_bar(close, high=close + 0.5, low=close - 0.5))
    # Make bar 14 drop sharply to cross below EMA
    bars[14] = _bar(103.0, high=104.0, low=102.5)
    return bars


class TestBearEmaRejectionSignal:
    def test_fires_on_cross_below_ema(self):
        bars = _intraday_ema_rejection()
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=14,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is not None

    def test_no_signal_when_uptrend(self):
        bars = _intraday_ema_rejection()
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=14,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is None

    def test_no_signal_when_no_cross(self):
        # All bars above EMA
        bars = [_bar(110.0 + i * 0.5, high=110.0 + i * 0.5 + 0.3, low=110.0 + i * 0.5 - 0.3)
                for i in range(15)]
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=14,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is None

    def test_no_signal_with_insufficient_bars(self):
        bars = [_bar(100.0) for _ in range(5)]
        result = evaluate_bear_ema_rejection_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(ema_period=9),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_ema_rejection.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_ema_rejection.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def _calculate_ema(bars: Sequence[Bar], period: int) -> float | None:
    if len(bars) < period:
        return None
    closes = [b.close for b in bars]
    ema = sum(closes[:period]) / period
    k = 2.0 / (period + 1)
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema


def evaluate_bear_ema_rejection_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    period = settings.ema_period
    if signal_index < period:
        return None
    current_ema = _calculate_ema(intraday_bars[: signal_index + 1], period)
    prior_ema = _calculate_ema(intraday_bars[:signal_index], period)
    if current_ema is None or prior_ema is None:
        return None
    signal_bar = intraday_bars[signal_index]
    prior_bar = intraday_bars[signal_index - 1]
    # Rejection: prior close >= EMA, current close < EMA
    if prior_bar.close < prior_ema or signal_bar.close >= current_ema:
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=rel_vol,
        stop_price=current_ema + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_ema_rejection_evaluator(
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
        equity_signal = evaluate_bear_ema_rejection_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_ema_rejection.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_ema_rejection.py tests/unit/test_bear_ema_rejection.py
git commit -m "feat: add bear_ema_rejection put strategy"
```

---

### Task 7: bear_vwap_breakdown

**Files:**
- Create: `src/alpaca_bot/strategy/bear_vwap_breakdown.py`
- Create: `tests/unit/test_bear_vwap_breakdown.py`

Signal: `signal_bar.high > vwap * (1 + threshold) AND signal_bar.close < vwap` (price touched above VWAP but closed below it) + downtrend filter.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_vwap_breakdown.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_vwap_breakdown import evaluate_bear_vwap_breakdown_signal, make_bear_vwap_breakdown_evaluator


def _bars_for_vwap_breakdown() -> list:
    """Several bars near 100, then signal bar that spikes above VWAP then closes below it."""
    # Uniform bars so VWAP ≈ 100
    bars = [_bar(100.0, high=100.5, low=99.5, volume=1000.0) for _ in range(4)]
    # threshold=0.015, so VWAP*(1+0.015)=101.5; signal.high=102 > 101.5; signal.close=98 < 100
    bars.append(_bar(98.0, high=102.0, low=97.5, volume=2000.0))
    return bars


class TestBearVwapBreakdownSignal:
    def test_fires_on_rejection_above_vwap(self):
        bars = _bars_for_vwap_breakdown()
        result = evaluate_bear_vwap_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_when_close_above_vwap(self):
        bars = [_bar(100.0, high=100.5, low=99.5, volume=1000.0) for _ in range(4)]
        bars.append(_bar(101.0, high=102.0, low=99.5, volume=2000.0))  # close above VWAP
        result = evaluate_bear_vwap_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_vwap_breakdown()
        result = evaluate_bear_vwap_breakdown_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_vwap_breakdown.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_vwap_breakdown.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.indicators import calculate_vwap
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_vwap_breakdown_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    today_bars = intraday_bars[: signal_index + 1]
    vwap = calculate_vwap(today_bars)
    if vwap is None:
        return None
    signal_bar = intraday_bars[signal_index]
    threshold = settings.vwap_dip_threshold_pct
    if signal_bar.high <= vwap * (1 + threshold) or signal_bar.close >= vwap:
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=rel_vol,
        stop_price=vwap + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_vwap_breakdown_evaluator(
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
        equity_signal = evaluate_bear_vwap_breakdown_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_vwap_breakdown.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_vwap_breakdown.py tests/unit/test_bear_vwap_breakdown.py
git commit -m "feat: add bear_vwap_breakdown put strategy"
```

---

### Task 8: bear_gap_and_drop

**Files:**
- Create: `src/alpaca_bot/strategy/bear_gap_and_drop.py`
- Create: `tests/unit/test_bear_gap_and_drop.py`

Signal fires only on bar 0 (first intraday bar). Conditions: `open < prior_close * (1 - gap_threshold_pct)` AND `close < prior_day_low`. Volume uses `gap_volume_threshold`. `prior_close` = `daily_bars[-2].close`, `prior_day_low` = `daily_bars[-2].low`.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_gap_and_drop.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_gap_and_drop import evaluate_bear_gap_and_drop_signal, make_bear_gap_and_drop_evaluator


def _bars_for_gap_drop() -> list:
    """
    Daily bars: prior_close=80 (bar[-2].close), prior_day_low=78 (bar[-2].low)
    _downtrend_daily_bars: closes=[110,110,110,110,110,80,80], lows=closes-2=[108,108,...,78,78]
    gap_threshold_pct=0.02 → open < 80*(1-0.02)=78.4
    signal: open=77, close=77.5 < 78 ✓, vol=2500 vs gap_volume_threshold=2.0 (avg=1000)
    """
    return [_bar(77.5, open=77.0, high=77.8, low=77.0, volume=2500.0)]


class TestBearGapAndDropSignal:
    def test_fires_on_gap_down_with_continuation(self):
        bars = _bars_for_gap_drop()
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_on_non_zero_index(self):
        bars = _bars_for_gap_drop() + [_bar(77.0)]
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=1,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_close_above_prior_day_low(self):
        # close=80, prior_day_low=78 → 80 > 78 → no signal
        bars = [_bar(80.0, open=77.0, high=80.5, low=76.5, volume=2500.0)]
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_gap_drop()
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_low_volume(self):
        bars = [_bar(77.5, open=77.0, high=77.8, low=77.0, volume=500.0)]
        result = evaluate_bear_gap_and_drop_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=0,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_gap_and_drop.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_gap_and_drop.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_gap_and_drop_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if signal_index != 0:
        return None
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    if len(daily_bars) < 2:
        return None
    prior_day = daily_bars[-2]
    prior_close = prior_day.close
    prior_day_low = prior_day.low
    signal_bar = intraday_bars[signal_index]
    if signal_bar.open >= prior_close * (1 - settings.gap_threshold_pct):
        return None
    if signal_bar.close >= prior_day_low:
        return None
    # Volume relative to gap_volume_threshold (absolute multiplier, baseline = 1 bar = prior day volume)
    if prior_day.volume <= 0 or signal_bar.volume / prior_day.volume < settings.gap_volume_threshold:
        return None
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=signal_bar.volume / prior_day.volume,
        stop_price=prior_day_low + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_gap_and_drop_evaluator(
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
        equity_signal = evaluate_bear_gap_and_drop_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

**Note on volume baseline:** The `gap_and_go.py` long strategy compares against a per-bar average computed from lookback bars; the bear version above uses `prior_day.volume` as the baseline since on bar 0 there are no prior intraday bars. This matches the intent of `gap_volume_threshold` (absolute multiplier). If the long strategy uses a different volume baseline, update accordingly after reading `gap_and_go.py` before the test suite runs.

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_gap_and_drop.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_gap_and_drop.py tests/unit/test_bear_gap_and_drop.py
git commit -m "feat: add bear_gap_and_drop put strategy"
```

---

### Task 9: bear_flag

**Files:**
- Create: `src/alpaca_bot/strategy/bear_flag.py`
- Create: `tests/unit/test_bear_flag.py`

Signal: drop (pole) → tight consolidation → break below consolidation low + downtrend filter. Pole: drop of >= `bull_flag_min_run_pct`. Consolidation: tight range (`< bull_flag_consolidation_range_pct * pole_open`) + low volume (`< bull_flag_consolidation_volume_ratio * avg_vol`). Signal fires when a bar closes below the consolidation low.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_flag.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_flag import evaluate_bear_flag_signal, make_bear_flag_evaluator


def _bars_for_bear_flag() -> list:
    """
    Pole: bars 0-2, drop from 110 to 107.8 (≥2% drop)
    Consolidation: bars 3-5, tight range around 108, low volume=400 < 0.6*1000=600
    Signal: bar 6, close=107 < consolidation_low≈107.5
    """
    # Pole bars (decline ≥ 2%)
    bars = [
        _bar(110.0, open=110.0, high=110.5, low=109.5, volume=1000.0),  # 0
        _bar(109.0, open=109.5, high=109.8, low=108.5, volume=1200.0),  # 1
        _bar(107.8, open=108.5, high=108.7, low=107.5, volume=1100.0),  # 2 — pole end
    ]
    # Consolidation bars (tight, low volume)
    for _ in range(3):
        bars.append(_bar(108.0, open=107.9, high=108.3, low=107.7, volume=400.0))
    # Signal bar: break below consolidation low (107.7)
    bars.append(_bar(107.0, open=107.6, high=107.8, low=106.8, volume=1500.0))
    return bars


class TestBearFlagSignal:
    def test_fires_on_bear_flag_break(self):
        bars = _bars_for_bear_flag()
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_bear_flag()
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_no_pole(self):
        # All bars flat — no drop >= 2%
        bars = [_bar(100.0, high=100.3, low=99.7, volume=1000.0) for _ in range(7)]
        result = evaluate_bear_flag_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_flag.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_flag.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract

_MIN_POLE_BARS = 2
_MIN_CONSOLIDATION_BARS = 2


def evaluate_bear_flag_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    if signal_index < _MIN_POLE_BARS + _MIN_CONSOLIDATION_BARS:
        return None
    signal_bar = intraday_bars[signal_index]
    # Find pole: look back for a drop >= bull_flag_min_run_pct in 2-4 bars
    pole_end = None
    for pole_len in range(_MIN_POLE_BARS, min(5, signal_index)):
        pole_start_idx = signal_index - pole_len - _MIN_CONSOLIDATION_BARS
        if pole_start_idx < 0:
            break
        pole_bars = intraday_bars[pole_start_idx : pole_start_idx + pole_len]
        pole_open = pole_bars[0].open
        pole_low = min(b.low for b in pole_bars)
        if pole_open <= 0:
            continue
        drop_pct = (pole_open - pole_low) / pole_open
        if drop_pct >= settings.bull_flag_min_run_pct:
            pole_end = pole_start_idx + pole_len
            break
    if pole_end is None:
        return None
    # Consolidation bars between pole end and signal
    consol_bars = intraday_bars[pole_end : signal_index]
    if len(consol_bars) < _MIN_CONSOLIDATION_BARS:
        return None
    consol_high = max(b.high for b in consol_bars)
    consol_low = min(b.low for b in consol_bars)
    consol_range = consol_high - consol_low
    pole_ref_price = intraday_bars[pole_end - 1 if pole_end > 0 else 0].close
    if pole_ref_price <= 0:
        return None
    if consol_range / pole_ref_price > settings.bull_flag_consolidation_range_pct:
        return None
    # Consolidation volume should be lower than average
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    consol_avg_vol = sum(b.volume for b in consol_bars) / len(consol_bars)
    if avg_vol > 0 and consol_avg_vol / avg_vol > settings.bull_flag_consolidation_volume_ratio:
        return None
    # Signal: close below consolidation low
    if signal_bar.close >= consol_low:
        return None
    pole_start_bar = intraday_bars[max(0, pole_end - _MIN_POLE_BARS)]
    entry_level = min(b.low for b in intraday_bars[max(0, pole_end - _MIN_POLE_BARS) : pole_end])
    rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=entry_level,
        relative_volume=rel_vol,
        stop_price=consol_high + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_flag_evaluator(
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
        equity_signal = evaluate_bear_flag_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_flag.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_flag.py tests/unit/test_bear_flag.py
git commit -m "feat: add bear_flag put strategy"
```

---

### Task 10: bear_vwap_cross_down

**Files:**
- Create: `src/alpaca_bot/strategy/bear_vwap_cross_down.py`
- Create: `tests/unit/test_bear_vwap_cross_down.py`

Signal: prior bar closed >= VWAP(bars[:-2]) AND signal bar closes < VWAP(bars[:-1]). Requires `signal_index >= 2`.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_vwap_cross_down.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_vwap_cross_down import evaluate_bear_vwap_cross_down_signal, make_bear_vwap_cross_down_evaluator


def _bars_for_vwap_cross_down() -> list:
    """
    Bars 0-2 at 100 (VWAP≈100). Bar 3 stays at 100 (prior close≥VWAP). Bar 4 drops to 97 (close<VWAP).
    VWAP(bars[0:4])≈100, prior bar=bars[3].close=100≥100 ✓
    VWAP(bars[0:5])≈(100*4+97)/5=99.6, signal bar=bars[4].close=97<99.6 ✓
    """
    bars = [_bar(100.0, high=100.5, low=99.5, volume=1000.0) for _ in range(4)]
    bars.append(_bar(97.0, high=99.5, low=96.5, volume=2000.0))
    return bars


class TestBearVwapCrossDownSignal:
    def test_fires_on_vwap_cross_down(self):
        bars = _bars_for_vwap_cross_down()
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_vwap_cross_down()
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_prior_bar_below_vwap(self):
        # prior bar already below VWAP — not a cross
        bars = [_bar(100.0, high=100.5, low=99.5, volume=1000.0) for _ in range(3)]
        bars.append(_bar(98.0, high=99.5, low=97.5, volume=1000.0))  # prior below VWAP
        bars.append(_bar(96.0, high=97.5, low=95.5, volume=2000.0))  # signal
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_with_insufficient_bars(self):
        bars = [_bar(100.0), _bar(98.0)]
        result = evaluate_bear_vwap_cross_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=1,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_vwap_cross_down.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_vwap_cross_down.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.indicators import calculate_vwap
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_vwap_cross_down_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    if signal_index < 2:
        return None
    today_bars = intraday_bars[: signal_index + 1]
    current_vwap = calculate_vwap(today_bars[:-1])  # VWAP through prior bar
    prior_vwap = calculate_vwap(today_bars[:-2])     # VWAP through bar before prior
    if current_vwap is None or prior_vwap is None:
        return None
    prior_bar = intraday_bars[signal_index - 1]
    signal_bar = intraday_bars[signal_index]
    # Cross down: prior close was >= prior_vwap, current close is < current_vwap
    if prior_bar.close < prior_vwap or signal_bar.close >= current_vwap:
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=rel_vol,
        stop_price=current_vwap + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_vwap_cross_down_evaluator(
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
        equity_signal = evaluate_bear_vwap_cross_down_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_vwap_cross_down.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_vwap_cross_down.py tests/unit/test_bear_vwap_cross_down.py
git commit -m "feat: add bear_vwap_cross_down put strategy"
```

---

### Task 11: bear_bb_squeeze_down

**Files:**
- Create: `src/alpaca_bot/strategy/bear_bb_squeeze_down.py`
- Create: `tests/unit/test_bear_bb_squeeze_down.py`

Signal: Bollinger Band squeeze (band width < `bb_squeeze_threshold_pct` for `bb_squeeze_min_bars` consecutive bars) followed by close < lower band + downtrend filter. `calculate_bollinger_bands` returns `(lower, mid, upper)` or `None`.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_bb_squeeze_down.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_bb_squeeze_down import evaluate_bear_bb_squeeze_down_signal, make_bear_bb_squeeze_down_evaluator


def _bars_for_bb_squeeze_down() -> list:
    """
    bb_period=5, bb_squeeze_min_bars=2, bb_squeeze_threshold_pct=0.03
    Need 5+2-1=6 bars minimum before signal bar (index 6).
    Bars 0-4: tight range around 100 (squeeze)
    Bars 5-6: signal bar at 6, close below lower band
    Use settings: bb_period=5, bb_std_dev=2.0, bb_squeeze_threshold_pct=0.03, bb_squeeze_min_bars=2
    """
    bars = [_bar(100.0 + (i % 3 - 1) * 0.1, volume=1000.0) for i in range(6)]
    bars.append(_bar(95.0, low=94.5, high=96.0, volume=2000.0))  # signal bar
    return bars


class TestBearBbSqueezeDownSignal:
    def test_fires_on_squeeze_and_downside_break(self):
        bars = _bars_for_bb_squeeze_down()
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_bb_squeeze_down()
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_close_above_lower_band(self):
        # signal bar close at 100.0 — should be above lower band
        bars = [_bar(100.0, volume=1000.0) for _ in range(6)]
        bars.append(_bar(100.0, high=100.5, low=99.5, volume=2000.0))
        result = evaluate_bear_bb_squeeze_down_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=6,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_bb_squeeze_down.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_bb_squeeze_down.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.indicators import calculate_bollinger_bands
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_bb_squeeze_down_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    min_required = settings.bb_period + settings.bb_squeeze_min_bars - 1
    if signal_index < min_required:
        return None
    signal_bar = intraday_bars[signal_index]
    # Check squeeze: band width < threshold for consecutive bars before signal
    for i in range(signal_index - settings.bb_squeeze_min_bars, signal_index):
        bb = calculate_bollinger_bands(intraday_bars[: i + 1], settings.bb_period, settings.bb_std_dev)
        if bb is None:
            return None
        lower, mid, upper = bb
        if mid <= 0:
            return None
        band_width = (upper - lower) / mid
        if band_width >= settings.bb_squeeze_threshold_pct:
            return None  # not a squeeze bar
    # Signal bar must close below lower band
    bb_signal = calculate_bollinger_bands(intraday_bars[: signal_index + 1], settings.bb_period, settings.bb_std_dev)
    if bb_signal is None:
        return None
    lower_signal, _, _ = bb_signal
    if signal_bar.close >= lower_signal:
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    rel_vol = signal_bar.volume / avg_vol if avg_vol > 0 else 1.0
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=signal_bar.close,
        relative_volume=rel_vol,
        stop_price=lower_signal + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_bb_squeeze_down_evaluator(
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
        equity_signal = evaluate_bear_bb_squeeze_down_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_bb_squeeze_down.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_bb_squeeze_down.py tests/unit/test_bear_bb_squeeze_down.py
git commit -m "feat: add bear_bb_squeeze_down put strategy"
```

---

### Task 12: bear_failed_breakout

**Files:**
- Create: `src/alpaca_bot/strategy/bear_failed_breakout.py`
- Create: `tests/unit/test_bear_failed_breakout.py`

Signal: price breaks prior session high then closes back below it (failed breakout → short). `prior_session_high = daily_bars[-2].high`. Conditions: `signal_bar.high > prior_session_high AND signal_bar.close < prior_session_high * (1 - buffer)`. Volume: `failed_breakdown_volume_ratio`.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_bear_failed_breakout.py
from tests.unit.bear_test_helpers import _bar, _settings, _downtrend_daily_bars, _uptrend_daily_bars, _put_contract
from alpaca_bot.strategy.bear_failed_breakout import evaluate_bear_failed_breakout_signal, make_bear_failed_breakout_evaluator


def _bars_for_failed_breakout() -> list:
    """
    _downtrend_daily_bars: bars[-2].high = 80+2=82 (prior session high)
    signal_bar: high=83 > 82 ✓, close=80 < 82*(1-0.001)=81.92 ✓
    vol: avg=1000, signal=2200 → 2.2 ≥ 2.0 ✓
    """
    bars = [_bar(82.0, volume=1000.0) for _ in range(4)]
    bars.append(_bar(80.0, high=83.0, low=79.5, volume=2200.0))
    return bars


class TestBearFailedBreakoutSignal:
    def test_fires_on_failed_breakout(self):
        bars = _bars_for_failed_breakout()
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is not None

    def test_no_signal_when_uptrend(self):
        bars = _bars_for_failed_breakout()
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_uptrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_high_does_not_exceed_prior_session_high(self):
        bars = [_bar(82.0, volume=1000.0) for _ in range(4)]
        bars.append(_bar(80.0, high=81.0, low=79.5, volume=2200.0))  # high=81 < 82
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_close_above_prior_session_high(self):
        bars = [_bar(82.0, volume=1000.0) for _ in range(4)]
        bars.append(_bar(83.0, high=84.0, low=81.0, volume=2200.0))  # close=83 > 82
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None

    def test_no_signal_when_low_volume(self):
        bars = [_bar(82.0, volume=1000.0) for _ in range(4)]
        bars.append(_bar(80.0, high=83.0, low=79.5, volume=1500.0))  # 1.5 < 2.0 threshold
        result = evaluate_bear_failed_breakout_signal(
            symbol="AAPL",
            intraday_bars=bars,
            signal_index=4,
            daily_bars=_downtrend_daily_bars(),
            settings=_settings(),
        )
        assert result is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/unit/test_bear_failed_breakout.py -v
```

- [ ] **Step 3: Implement**

```python
# src/alpaca_bot/strategy/bear_failed_breakout.py
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from alpaca_bot.config import Settings
from alpaca_bot.domain.models import Bar, EntrySignal, OptionContract
from alpaca_bot.strategy.breakout import daily_downtrend_filter_passes
from alpaca_bot.strategy.option_selector import select_put_contract


def evaluate_bear_failed_breakout_signal(
    *,
    symbol: str,
    intraday_bars: Sequence[Bar],
    signal_index: int,
    daily_bars: Sequence[Bar],
    settings: Settings,
) -> EntrySignal | None:
    if not daily_downtrend_filter_passes(daily_bars, settings):
        return None
    if len(daily_bars) < 2:
        return None
    prior_session_high = daily_bars[-2].high
    signal_bar = intraday_bars[signal_index]
    if signal_bar.high <= prior_session_high:
        return None
    buffer = settings.failed_breakdown_recapture_buffer_pct
    if signal_bar.close >= prior_session_high * (1 - buffer):
        return None
    vol_lookback = settings.relative_volume_lookback_bars
    if signal_index < vol_lookback:
        return None
    avg_vol = sum(b.volume for b in intraday_bars[signal_index - vol_lookback : signal_index]) / vol_lookback
    if avg_vol <= 0 or signal_bar.volume / avg_vol < settings.failed_breakdown_volume_ratio:
        return None
    rel_vol = signal_bar.volume / avg_vol
    return EntrySignal(
        symbol=symbol,
        signal_bar=signal_bar,
        entry_level=prior_session_high,
        relative_volume=rel_vol,
        stop_price=signal_bar.high + settings.entry_stop_price_buffer,
        limit_price=0.0,
        initial_stop_price=0.01,
        option_contract=None,
    )


def make_bear_failed_breakout_evaluator(
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
        equity_signal = evaluate_bear_failed_breakout_signal(
            symbol=symbol,
            intraday_bars=intraday_bars,
            signal_index=signal_index,
            daily_bars=daily_bars,
            settings=settings,
        )
        if equity_signal is None:
            return None
        today = intraday_bars[-1].timestamp.astimezone(settings.market_timezone).date()
        contract = select_put_contract(
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

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_bear_failed_breakout.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/alpaca_bot/strategy/bear_failed_breakout.py tests/unit/test_bear_failed_breakout.py
git commit -m "feat: add bear_failed_breakout put strategy"
```

---

### Task 13: Strategy registry + supervisor dispatch

**Files:**
- Modify: `src/alpaca_bot/strategy/__init__.py`
- Modify: `src/alpaca_bot/runtime/supervisor.py`
- Create: `tests/unit/test_bear_registry.py`

- [ ] **Step 1: Write failing registry test**

```python
# tests/unit/test_bear_registry.py
from alpaca_bot.strategy import OPTION_STRATEGY_FACTORIES, OPTION_STRATEGY_NAMES

_EXPECTED_STRATEGIES = {
    "breakout_calls",
    "bear_breakdown",
    "bear_momentum",
    "bear_orb",
    "bear_low_watermark",
    "bear_ema_rejection",
    "bear_vwap_breakdown",
    "bear_gap_and_drop",
    "bear_flag",
    "bear_vwap_cross_down",
    "bear_bb_squeeze_down",
    "bear_failed_breakout",
}


def test_all_strategies_registered():
    assert _EXPECTED_STRATEGIES == set(OPTION_STRATEGY_FACTORIES.keys())


def test_option_strategy_names_matches_factories():
    assert OPTION_STRATEGY_NAMES == frozenset(OPTION_STRATEGY_FACTORIES.keys())


def test_all_factories_are_callable():
    for name, factory in OPTION_STRATEGY_FACTORIES.items():
        assert callable(factory), f"{name} factory is not callable"
```

- [ ] **Step 2: Run test to confirm failure**

```bash
pytest tests/unit/test_bear_registry.py -v
```

- [ ] **Step 3: Update `strategy/__init__.py`**

Read the current `src/alpaca_bot/strategy/__init__.py` and replace the `OPTION_STRATEGY_NAMES` definition with:

```python
from collections.abc import Callable

from alpaca_bot.strategy.bear_bb_squeeze_down import make_bear_bb_squeeze_down_evaluator
from alpaca_bot.strategy.bear_breakdown import make_bear_breakdown_evaluator
from alpaca_bot.strategy.bear_ema_rejection import make_bear_ema_rejection_evaluator
from alpaca_bot.strategy.bear_failed_breakout import make_bear_failed_breakout_evaluator
from alpaca_bot.strategy.bear_flag import make_bear_flag_evaluator
from alpaca_bot.strategy.bear_gap_and_drop import make_bear_gap_and_drop_evaluator
from alpaca_bot.strategy.bear_low_watermark import make_bear_low_watermark_evaluator
from alpaca_bot.strategy.bear_momentum import make_bear_momentum_evaluator
from alpaca_bot.strategy.bear_orb import make_bear_orb_evaluator
from alpaca_bot.strategy.bear_vwap_breakdown import make_bear_vwap_breakdown_evaluator
from alpaca_bot.strategy.bear_vwap_cross_down import make_bear_vwap_cross_down_evaluator
from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator

OPTION_STRATEGY_FACTORIES: dict[str, Callable] = {
    "breakout_calls": make_breakout_calls_evaluator,
    "bear_breakdown": make_bear_breakdown_evaluator,
    "bear_momentum": make_bear_momentum_evaluator,
    "bear_orb": make_bear_orb_evaluator,
    "bear_low_watermark": make_bear_low_watermark_evaluator,
    "bear_ema_rejection": make_bear_ema_rejection_evaluator,
    "bear_vwap_breakdown": make_bear_vwap_breakdown_evaluator,
    "bear_gap_and_drop": make_bear_gap_and_drop_evaluator,
    "bear_flag": make_bear_flag_evaluator,
    "bear_vwap_cross_down": make_bear_vwap_cross_down_evaluator,
    "bear_bb_squeeze_down": make_bear_bb_squeeze_down_evaluator,
    "bear_failed_breakout": make_bear_failed_breakout_evaluator,
}

OPTION_STRATEGY_NAMES: frozenset[str] = frozenset(OPTION_STRATEGY_FACTORIES.keys())
```

Remove the old `OPTION_STRATEGY_NAMES = frozenset({"breakout_calls"})` line and the old `from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator` import (now included in the block above).

- [ ] **Step 4: Update supervisor dispatch**

In `src/alpaca_bot/runtime/supervisor.py`:

**Change 1** — update imports at top to also import `OPTION_STRATEGY_FACTORIES`:

```python
from alpaca_bot.strategy import (
    OPTION_STRATEGY_FACTORIES,
    OPTION_STRATEGY_NAMES,
    STRATEGY_NAMES,
    STRATEGY_FACTORIES,
)
```

Remove the line `from alpaca_bot.strategy.breakout_calls import make_breakout_calls_evaluator` if present.

**Change 2** — replace the hardcoded factory loop (around line 609):

Old:
```python
for opt_name in OPTION_STRATEGY_NAMES:
    active_strategies.append(
        (opt_name, make_breakout_calls_evaluator(option_chains_by_symbol))
    )
```

New:
```python
for opt_name in OPTION_STRATEGY_NAMES:
    factory = OPTION_STRATEGY_FACTORIES[opt_name]
    active_strategies.append(
        (opt_name, factory(option_chains_by_symbol))
    )
```

**Change 3** — bypass regime filter for option strategies (around line 689 where `regime_bars` is passed to the cycle runner):

Find the line that passes `regime_bars` to `cycle_runner` or `run_cycle`. Add:

```python
strategy_regime_bars = None if strategy_name in OPTION_STRATEGY_NAMES else regime_bars
```

And pass `strategy_regime_bars` in place of `regime_bars` for the per-strategy call.

Note: the exact line numbers will vary. Read the file and find the loop that calls `cycle_runner` per strategy, then apply the change.

- [ ] **Step 5: Run registry tests**

```bash
pytest tests/unit/test_bear_registry.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
pytest
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/alpaca_bot/strategy/__init__.py \
        src/alpaca_bot/runtime/supervisor.py \
        tests/unit/test_bear_registry.py
git commit -m "feat: register all bear strategies in OPTION_STRATEGY_FACTORIES; dispatch by registry in supervisor"
```

---

### Task 14: DEPLOYMENT.md update

**Files:**
- Modify: `DEPLOYMENT.md`

- [ ] **Step 1: Add inverse ETF config note**

In `DEPLOYMENT.md`, find the `SYMBOLS=AAPL,MSFT,SPY` example line (around line 39) and add a comment below it:

```dotenv
# Add inverse ETFs for bearish market exposure (traded as equity, not options):
# SYMBOLS=AAPL,MSFT,SPY,SQQQ,SPXS,SOXS
# SQQQ=3× inverse Nasdaq, SPXS=3× inverse S&P 500, SOXS=3× inverse semiconductors
```

- [ ] **Step 2: Add bear strategy options note**

In `DEPLOYMENT.md`, find the options trading section (around line 79):

```dotenv
# Options trading (disabled by default; set ENABLE_OPTIONS_TRADING=true to activate)
# ENABLE_OPTIONS_TRADING=false
```

Add after the existing options lines:

```dotenv
# Bearish put strategies are included automatically when ENABLE_OPTIONS_TRADING=true.
# 11 strategies mirror all long strategies with inverted signal logic.
# No additional config needed — put strategies activate with option trading.
```

- [ ] **Step 3: Commit**

```bash
git add DEPLOYMENT.md
git commit -m "docs: document inverse ETF config and bear strategy activation in DEPLOYMENT.md"
```

---

## Final verification

```bash
pytest
```

All tests should pass. The 11 bearish put strategies are now registered, tested, and dispatched by the supervisor. Inverse ETF longs require only operator config — no code changes needed.

# Bearish Strategies Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

## Goal

Add strategies that profit from market downturns alongside the existing long-only strategies, so the bot generates returns regardless of market direction. Two complementary mechanisms:

1. **Inverse ETF longs (zero code)** — add SQQQ, SPXS, SOXS to `SYMBOLS`. The existing long breakout strategy captures these when the market falls.
2. **Put option strategies (11 new strategies)** — bearish mirror of every existing long strategy, each buying puts on stocks that break down.

---

## Architecture Overview

Five change areas:

| Area | Files | What changes |
|---|---|---|
| 1. Put contract selector | `strategy/option_selector.py` | Add `select_put_contract()` |
| 2. Bearish signal + factory files | `strategy/bear_*.py` (11 new files) | Signal logic + factory for each strategy |
| 3. Shared trend filter | `strategy/breakout.py` | Add `daily_downtrend_filter_passes()` |
| 4. Strategy registry | `strategy/__init__.py` | Add to `OPTION_STRATEGY_NAMES`, add `OPTION_STRATEGY_FACTORIES` |
| 5. Supervisor dispatch | `runtime/supervisor.py` | Use `OPTION_STRATEGY_FACTORIES` for dispatch, skip regime filter for puts |

**No changes** to `domain/models.py`, `core/engine.py`, or the option EOD flatten path — puts use the existing option order machinery unchanged.

---

## Detailed Design

### How Put Strategies Fit the Existing Architecture

Put option strategies follow the same path as `breakout_calls`:

1. Each strategy is a factory `make_bear_*_evaluator(option_chains_by_symbol)` → evaluator callable
2. Evaluator runs bearish signal detection on the underlying stock; if triggered, selects a put contract and returns `EntrySignal(option_contract=OptionContract(option_type="put", ...))`
3. Engine emits `CycleIntent(ENTRY, is_option=True, option_type_str="put")` — same path as calls
4. Runtime creates an `OptionOrderRecord` with `side="buy"` and `option_type="put"` in the `option_orders` table
5. EOD flatten: existing supervisor path (line 830–862 in `supervisor.py`) sells all open option positions at EOD
6. Same-underlying-symbol guard: supervisor already adds `opt_pos.underlying_symbol` to `working_order_symbols`, blocking duplicate entries on the same underlying from any other strategy

**No stop orders are placed on put contracts** — same as `breakout_calls`. Position management is EOD-flatten only. Puts are held until 15:45 ET then sold at market.

### 1. Put Contract Selector (`strategy/option_selector.py`)

Add alongside the existing `select_call_contract()`:

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
        # For puts, delta is negative; take abs for comparison against target
        return min(with_delta, key=lambda c: abs(abs(c.delta) - settings.option_delta_target))
    return min(eligible, key=lambda c: abs(c.strike - current_price))
```

### 2. Shared Downtrend Filter (`strategy/breakout.py`)

Add alongside the existing `daily_trend_filter_passes()`:

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

### 3. Bearish Strategy Files (`strategy/bear_*.py`)

Each file exports:
- A pure signal function `evaluate_bear_*_signal()` — same signature as equity strategy evaluators but with inverted logic
- A factory `make_bear_*_evaluator(option_chains_by_symbol)` — wraps signal + `select_put_contract()`

**Common factory pattern** (repeated across all 11 files):

```python
def make_bear_*_evaluator(
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
        equity_signal = evaluate_bear_*_signal(
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

#### Signal logic for each of the 11 strategies

| File | Long counterpart | Breakdown signal |
|---|---|---|
| `bear_breakdown.py` | `breakout.py` | `signal_bar.low < min(intraday_bars[i-lookback:i].low)` + rel-vol + downtrend filter |
| `bear_momentum.py` | `momentum.py` | 3+ consecutive down-bars (each close < prior close) + downtrend filter |
| `bear_orb.py` | `orb.py` | Close below opening-range low (first N bars) after entry window opens + downtrend filter |
| `bear_low_watermark.py` | `high_watermark.py` | New session low + downtrend filter |
| `bear_ema_rejection.py` | `ema_pullback.py` | Price crosses EMA from above (rejection) + downtrend filter |
| `bear_vwap_breakdown.py` | `vwap_reversion.py` | Price crosses below VWAP and closes under it + downtrend filter |
| `bear_gap_and_drop.py` | `gap_and_go.py` | Gap down (open < prior close by threshold) + follow-through continuation bar + downtrend filter |
| `bear_flag.py` | `bull_flag.py` | Bear flag: drop followed by tight consolidation then break below consolidation low + downtrend filter |
| `bear_vwap_cross_down.py` | `vwap_cross.py` | VWAP cross downward + close below VWAP + downtrend filter |
| `bear_bb_squeeze_down.py` | `bb_squeeze.py` | Bollinger Band squeeze + downside break (close < lower band) + downtrend filter |
| `bear_failed_breakout.py` | `failed_breakdown.py` | Failed breakout → reversal: price breaks N-bar high then closes back inside + downtrend filter |

**ATR stop for puts**: stop is placed ABOVE the breakdown level — `entry_level + atr_stop_buffer`. This is stored in `equity_signal.stop_price` within the signal function (consistent with how long strategies store the stop level in the signal), but `initial_stop_price=0.01` is passed to `EntrySignal` for the option order (no stop order on the contract itself).

**Downtrend filter**: all 11 bearish strategies call `daily_downtrend_filter_passes()` (requires `prior_close < SMA`). Inverse ETF symbols (SQQQ, SPXS, SOXS) are excluded from downtrend filter — their price moves inversely to the underlying, so the normal breakout strategy handles them without filter inversion.

### 4. Strategy Registry (`strategy/__init__.py`)

Add `OPTION_STRATEGY_FACTORIES` alongside the existing registries:

```python
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

`OPTION_STRATEGY_NAMES` is derived from `OPTION_STRATEGY_FACTORIES` keys so they stay in sync automatically.

### 5. Supervisor Dispatch (`runtime/supervisor.py`)

**Current** (hardcoded factory):
```python
for opt_name in OPTION_STRATEGY_NAMES:
    active_strategies.append(
        (opt_name, make_breakout_calls_evaluator(option_chains_by_symbol))
    )
```

**New** (dispatch via registry):
```python
for opt_name in OPTION_STRATEGY_NAMES:
    factory = OPTION_STRATEGY_FACTORIES[opt_name]
    active_strategies.append(
        (opt_name, factory(option_chains_by_symbol))
    )
```

**Regime filter bypass for option strategies**: pass `regime_bars=None` for strategies in `OPTION_STRATEGY_NAMES`. Bearish puts should be enabled when the market is in a downtrend (the regime filter would incorrectly block them). Calls already have their own bullish daily trend filter inside the signal logic, making the regime filter redundant for them too.

```python
strategy_regime_bars = None if strategy_name in OPTION_STRATEGY_NAMES else regime_bars
# ... pass strategy_regime_bars instead of regime_bars in the cycle_runner call
```

### 6. Inverse ETF Config

Zero code changes. Operator adds to `SYMBOLS` in the env file:

```dotenv
SYMBOLS=AAPL,MSFT,SPY,SQQQ,SPXS,SOXS
```

`SQQQ` (3× inverse Nasdaq), `SPXS` (3× inverse S&P 500), `SOXS` (3× inverse semiconductors) benefit from existing long breakout when the market falls. These are traded as equity positions, not options.

---

## Backward Compatibility

- All 11 new bearish strategies are added to `OPTION_STRATEGY_NAMES`. They are **only active when `ENABLE_OPTIONS_TRADING=true`** — the `_option_chain_adapter` check in supervisor already gates them.
- `OPTION_STRATEGY_FACTORIES` is a new export from `strategy/__init__.py`. It does not break existing imports.
- `OPTION_STRATEGY_NAMES` is redefined to derive from `OPTION_STRATEGY_FACTORIES.keys()` — same frozenset, same values.
- Existing deployments without `ENABLE_OPTIONS_TRADING=true` see zero behavior change.

---

## Out of Scope

- Stop orders on put contracts (EOD flatten is the exit mechanism, same as `breakout_calls`)
- Intraday stop tracking against the underlying stock price (future enhancement)
- Per-strategy regime inversion flag (the `None` regime_bars approach is sufficient)
- Short selling equities — all bearish exposure is via put options
- Gamma scalping or delta hedging — each put is held to EOD and sold outright

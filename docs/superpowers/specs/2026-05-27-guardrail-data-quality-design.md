# Guardrail Data Quality and Observability Design

## Goal

Close three data-quality and observability gaps identified by auditing the guardrail and trading logic pipeline:

1. **Sizing rejection silent failure** — when `calculate_position_size` returns 0, the engine silently skips the entry with no `DecisionRecord`, making sizing failures invisible in the decision log.
2. **Stale daily bars used for ATR at entry** — the engine checks daily bar age for viability exits but not for new entries; stale bars (present but old) produce incorrect ATR stops without triggering the existing None-guard.
3. **Zero-close bar in ATR** — a bar with `close <= 0` or `volume < 0` from the data feed passes unchecked into ATR calculation and SMA filters, producing corrupted stop distances.

## Architecture

All three improvements are confined to `risk/sizing.py` and `core/engine.py`. No migrations, no new Settings fields, no new config vars. They follow the existing `DecisionRecord(reject_stage=..., reject_reason=...)` pattern already used for `regime_blocked`, `vix_blocked`, `sector_blocked`, and `capacity_full`.

## Tech Stack

Python, existing `DecisionRecord` dataclass, existing `viability_daily_bar_max_age_days` setting.

---

## Component 1: Sizing Rejection DecisionRecord

### Problem

`engine.py` lines 884–890:

```python
if quantity <= 0.0:
    continue   # silent — no DecisionRecord emitted
if (
    settings.min_position_notional > 0
    and quantity * signal.limit_price < settings.min_position_notional
):
    continue   # silent — no DecisionRecord emitted
```

Operators cannot distinguish "no signal" from "signal but priced out / sized to zero" when reviewing `decision_records`.

### Design

`calculate_position_size` in `risk/sizing.py` keeps its current `float` return type — callers in `replay/runner.py`, `test_strategy_rules.py`, and `test_position_sizing.py` must not be broken. The rejection reason is instead derived in `engine.py` from the inputs at the call site.

**`engine.py` change** — emit DecisionRecord for sizing=0 and below_min_notional.

The `reject_reason` for a zero-quantity result is determined by inspecting whether `max_loss_per_trade_dollars` is set and whether the risk-per-share-based quantity would have been at least 1. A single `"quantity_zero"` reason is sufficient for operator diagnosis — the `stop_price`, `limit_price`, and `equity` fields in the DecisionRecord provide the context to trace the cause.

```python
quantity = calculate_position_size(
    equity=equity,
    entry_price=signal.limit_price,
    stop_price=effective_initial_stop,
    settings=settings,
    fractionable=fractionable,
)
if quantity <= 0.0:
    _decision_records.append(DecisionRecord(
        cycle_at=now,
        symbol=symbol,
        strategy_name=strategy_name,
        trading_mode=_tm,
        strategy_version=_sv,
        decision="rejected",
        reject_stage="sizing",
        reject_reason="quantity_zero",
        entry_level=signal.entry_level,
        signal_bar_close=signal.signal_bar.close,
        relative_volume=signal.relative_volume,
        atr=None,
        stop_price=signal.stop_price,
        limit_price=signal.limit_price,
        initial_stop_price=effective_initial_stop,
        quantity=0.0,
        risk_per_share=round(signal.limit_price - effective_initial_stop, 4),
        equity=equity,
        filter_results={},
        vix_close=_ctx_vix_close,
        vix_above_sma=_ctx_vix_above_sma,
        sector_passing_pct=_ctx_sector_passing_pct,
    ))
    continue
if (
    settings.min_position_notional > 0
    and quantity * signal.limit_price < settings.min_position_notional
):
    _decision_records.append(DecisionRecord(
        cycle_at=now,
        symbol=symbol,
        strategy_name=strategy_name,
        trading_mode=_tm,
        strategy_version=_sv,
        decision="rejected",
        reject_stage="sizing",
        reject_reason="below_min_notional",
        entry_level=signal.entry_level,
        signal_bar_close=signal.signal_bar.close,
        relative_volume=signal.relative_volume,
        atr=None,
        stop_price=signal.stop_price,
        limit_price=signal.limit_price,
        initial_stop_price=effective_initial_stop,
        quantity=quantity,
        risk_per_share=round(signal.limit_price - effective_initial_stop, 4),
        equity=equity,
        filter_results={},
        vix_close=_ctx_vix_close,
        vix_above_sma=_ctx_vix_above_sma,
        sector_passing_pct=_ctx_sector_passing_pct,
    ))
    continue
```

### Backward compatibility

`calculate_position_size` signature is unchanged. No callers (`engine.py`, `replay/runner.py`, `test_position_sizing.py`, `test_strategy_rules.py`) need to be modified for the signature change. Bear-option paths use `calculate_option_position_size` (unchanged).

---

## Component 2: Stale Daily Bar Guard at Entry

### Problem

The engine checks daily bar age for **viability exits** (line 218–221) but not for **new entries**. A stale daily bar (e.g., from an API outage or a long weekend) passes into `atr_stop_buffer()` and produces a stop distance based on old ATR. The existing `calculate_atr() is None` guard in strategies only catches missing/insufficient bars, not stale-but-present bars.

### Design

Add a daily bar age check in `engine.py` before calling `signal_evaluator()`, symmetric with the viability exit check. If `daily_bars` are too old, emit a `DecisionRecord` with `reject_stage="stale_data"`, `reject_reason="daily_bars_stale"` and skip the entry.

**Location:** After `bar_age_seconds` stale-bar check (line 712) and before `signal_evaluator()` call (line 756).

```python
# Guard: stale daily bars — same threshold as viability exit check
if daily_bars:
    daily_bar_age_days = (now - daily_bars[-1].timestamp.astimezone(timezone.utc)).days
    if daily_bar_age_days > settings.viability_daily_bar_max_age_days:
        _decision_records.append(DecisionRecord(
            ...,
            decision="rejected",
            reject_stage="stale_data",
            reject_reason="daily_bars_stale",
            ...
        ))
        continue
```

### Behavior contract

- Only fires if `daily_bars` is non-empty and the latest bar is older than `viability_daily_bar_max_age_days` (default 5) days.
- If `daily_bars` is empty, the existing `if not bars or not daily_bars: continue` guard at line 680 already handles it.
- Does NOT affect extended-hours sessions differently — the age check applies unconditionally during any session type.
- The ATR None-guard in strategies (e.g. `if calculate_atr(daily_bars, settings.atr_period) is None: return None`) is a complementary but orthogonal check: it catches insufficient bar count; this new guard catches stale bars.

---

## Component 3: Zero-Close Bar Filter

### Problem

`_parse_barset` in `execution/alpaca.py` is a raw passthrough. A bar with `close <= 0` from the data feed enters the engine unchecked. In `calculate_atr`, `_tr(i) = max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close))` — a zero previous close produces an inflated `abs(bar.high - 0)` term, corrupting the ATR.

### Design

Add a private helper `_filter_valid_bars(bars, *, label="")` in `engine.py` that:
1. Filters out bars where `close <= 0` (corrupt price data). Zero-volume daily bars are valid for illiquid tickers and are NOT filtered.
2. Logs a `logger.warning` if any bars were dropped.
3. Returns the filtered sequence.

Call this helper on `daily_bars` before using them for ATR or trend filter evaluation (applies inside the per-symbol entry loop).

```python
def _filter_valid_bars(bars: Sequence[Bar], *, label: str = "") -> tuple[Bar, ...]:
    valid = tuple(b for b in bars if b.close > 0)
    if len(valid) < len(bars):
        logger.warning(
            "_filter_valid_bars: dropped %d zero-close bars%s",
            len(bars) - len(valid),
            f" for {label}" if label else "",
        )
    return valid
```

**Usage in engine.py** (inside the entry evaluation loop, after fetching `daily_bars`):
```python
daily_bars = daily_bars_by_symbol.get(symbol, ())
daily_bars = _filter_valid_bars(daily_bars, label=symbol)
```

### Constraints

- Only applied to `daily_bars` in the entry evaluation loop. Intraday `bars` are not filtered (the relative volume average-volume guard already suppresses signals when volume is zero).
- No change to the `atr.py` module itself — the fix is at the call site.
- Does not affect the ATR computation inside viability exits (uses `daily_bars_pos` fetched separately — that path is also guarded by the existing age check, so stale/corrupt data is either filtered or age-blocked).

---

## Testing Strategy

**Component 1 — `test_position_sizing.py`:**
- Update all existing tests to unpack `(qty, reason)` tuple.
- Add parametrized tests: `risk_per_share_zero`, `quantity_below_1`, `dollar_cap_quantity_below_1`, each asserting the correct `reason` string.
- In `test_cycle_engine.py` or new `test_engine_sizing_rejection.py`: build a scenario where the signal has a stop too close to entry, assert a `DecisionRecord` with `reject_stage="sizing"` is emitted and `decision="rejected"`.

**Component 2 — `test_cycle_engine.py`:**
- Add a test where `daily_bars[-1].timestamp` is older than `viability_daily_bar_max_age_days + 1` days; assert entry is skipped and a `DecisionRecord` with `reject_stage="stale_data"`, `reject_reason="daily_bars_stale"` is present in `cycle_result.decision_records`.

**Component 3 — `test_cycle_engine.py` or `test_engine_guardrails.py`:**
- Add `daily_bars` containing one bar with `close=0.0`; assert it is filtered before ATR computation (zero-close bar removed, remaining bars used).
- Assert a `logger.warning` is emitted (use `caplog`).

---

## Error Handling

- Component 1: `calculate_position_size` only raises `ValueError` for `stop_price >= entry_price` (unchanged). All soft failures return `(0.0, reason_string)`.
- Component 2: `daily_bar_age_days` check uses `.days` on a `timedelta`, which is always non-negative. No new exceptions.
- Component 3: `_filter_valid_bars` never raises; it is a simple comprehension.

---

## Out of Scope

- Per-strategy consecutive loss gate (architectural guardrail change, separate spec)
- Short option exposure in `MAX_PORTFOLIO_EXPOSURE_PCT` (options use premium notional, which is already risk-bounded for long options)
- Ingestion-level validation in `execution/alpaca.py` (higher risk, separate concern)
- Intraday bar zero-volume filter (relative volume guard already suppresses signals when average volume is zero)

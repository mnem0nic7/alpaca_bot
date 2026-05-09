---
title: Profitability Improvements — Profit Target, Expectancy Display, Trend Filter Debounce
date: 2026-05-09
status: approved
---

# Profitability Improvements

## Background

A profitability audit identified three active gaps in the bot's edge definition:

1. **No profit target.** Winning trades have undefined upside — they exit at EOD flatten
   (15:45 ET) or when a stop is hit. Risk/reward ratio is unknown and possibly negative.
2. **No expectancy display.** `BacktestReport` already computes `win_rate`,
   `avg_win_return_pct`, and `avg_loss_return_pct`, but does not combine them into a
   single expectancy figure, making it hard to quickly judge whether a strategy is
   profitable.
3. **Trend filter exit fires on 1-day whipsaws.** When `ENABLE_TREND_FILTER_EXIT=true`,
   a single daily close below the SMA triggers an exit. A debounce period (N consecutive
   days below SMA) reduces false exits.

## Out of Scope (Deferred)

- **Partial profit-taking / scale-out:** Requires Postgres schema changes and new order
  dispatch paths. Separate project.
- **Adaptive volume filter:** Requires per-symbol volume history and regime detection.
  Separate project.
- **Running backtests on historical data:** Operational task; requires external data feed.
- **Stop cap / regime filter tuning:** Already env-var configurable. Operator config, not
  code.

---

## Feature 1 — Profit Target (Take-Profit at N×R)

### Goal

Exit a position when it reaches a configurable multiple of the initial risk. Default
disabled so existing behaviour is unchanged on deploy.

### Settings (two new fields on `Settings` frozen dataclass)

```
ENABLE_PROFIT_TARGET  bool    default: False
PROFIT_TARGET_R       float   default: 2.0    # exit at entry + 2 × risk_per_share
```

Validation: `PROFIT_TARGET_R > 0`.

### Engine Logic

In `evaluate_cycle()` → per-position loop, immediately after the extended-hours guard
(which does `continue` for extended sessions), add:

```python
if settings.enable_profit_target and not is_extended:
    target_price = round(
        position.entry_price + settings.profit_target_r * position.risk_per_share, 2
    )
    if latest_bar.high >= target_price:
        intents.append(CycleIntent(
            intent_type=CycleIntentType.EXIT,
            symbol=position.symbol,
            timestamp=latest_bar.timestamp,
            reason="profit_target",
            strategy_name=strategy_name,
        ))
        emitted_exit_symbols.add(position.symbol)
        continue
```

The check runs before stop-update passes so a position that hits both the trailing-stop
trigger and the profit target in the same bar exits cleanly (no dangling UPDATE_STOP).

### Exit Reason Propagation

`exit_reason: str` is a free string tracked in:
- `replay/report.py` `TradeRecord`
- `replay/runner.py` (replay fill detection)
- `admin/session_eval_cli.py` (live session reconstruction)
- `web/service.py` `ClosedTradeRecord`

All four sites currently distinguish `"stop"` vs `"eod"`. A third value `"profit_target"`
is added throughout. `BacktestReport` gets new counters:

```python
profit_target_wins: int = 0
profit_target_losses: int = 0
```

### Replay Fill Simulation

In `replay/runner.py`, the replay stop-hit check currently uses `bar.low <= stop_price`.
Add a symmetric take-profit check: if `bar.high >= target_price`, the trade exits at
`target_price` (exact fill). If both stop and target are hit within the same bar (rare
gap scenario), stop takes priority (conservative).

### Session Eval (Live)

`session_eval_cli.py` reconstructs trades from audit events. Add `"profit_target"` as a
recognized `intent_type` value alongside the existing `"stop"` path. Until the engine
emits a dedicated audit event type for profit-target exits, the `reason` field on the
`AuditEvent` is used for display only; the `intent_type` recorded for the closed order
remains `"exit"`.

---

## Feature 2 — Expectancy Display

### Goal

Show a single number in `BacktestReport` that immediately communicates whether a strategy
has positive expected value per trade.

### Formula

```
expectancy_pct = win_rate × avg_win_return_pct + (1 - win_rate) × avg_loss_return_pct
```

`avg_loss_return_pct` is already stored as a negative value, so the formula naturally
produces the signed per-trade weighted average return. `None` when `win_rate` is `None`
(zero trades) or when either avg component is missing.

### Changes

**`replay/report.py` — `BacktestReport` dataclass**
```python
expectancy_pct: float | None = None
```

**`replay/report.py` — `build_report()`**
```python
if (report.win_rate is not None
        and report.avg_win_return_pct is not None
        and report.avg_loss_return_pct is not None):
    expectancy_pct = (
        report.win_rate * report.avg_win_return_pct
        + (1 - report.win_rate) * report.avg_loss_return_pct
    )
```

**`admin/session_eval_cli.py` — summary print block**
```
 Expectancy: +0.34%   (positive = edge exists)
```

**`replay/cli.py` — JSON and table output**
Add `expectancy_pct` to the per-strategy summary rows.

---

## Feature 3 — Trend Filter Exit Debounce

### Goal

When `ENABLE_TREND_FILTER_EXIT=true`, prevent a single bad day from triggering an exit.
Require that the symbol's daily close has been below the SMA for N consecutive days.

### Setting (one new field)

```
TREND_FILTER_EXIT_LOOKBACK_DAYS   int   default: 1   # current behaviour preserved
```

Validation: `>= 1`.

### Implementation

Add a new function `daily_trend_filter_exit_passes` in `strategy/breakout.py`:

```python
def daily_trend_filter_exit_passes(
    daily_bars: Sequence[Bar], settings: Settings
) -> bool:
    """Returns False when the last TREND_FILTER_EXIT_LOOKBACK_DAYS closes are all
    below the daily SMA — meaning exit is warranted.  Returns True (hold) otherwise.
    """
    n = settings.trend_filter_exit_lookback_days
    required = settings.daily_sma_period + n  # need SMA + n completed bars
    if len(daily_bars) < required + 1:        # +1 for partial current bar
        return True  # insufficient history → don't exit
    for offset in range(n):
        # offset=0: latest completed bar; offset=1: day before; etc.
        window_end = -1 - offset            # exclude partial current bar
        window_start = window_end - settings.daily_sma_period
        window = daily_bars[window_start:window_end]
        sma = sum(b.close for b in window) / len(window)
        close = daily_bars[window_end - 1].close
        if close > sma:
            return True  # at least one day above SMA → hold
    return False  # all N days below SMA → exit warranted
```

Replace the existing call in `engine.py` line ~185:
```python
# old
if not daily_trend_filter_passes(daily_bars_pos, settings):
# new
if not daily_trend_filter_exit_passes(daily_bars_pos, settings):
```

`daily_trend_filter_passes` (used for entries in `breakout.py`, `momentum.py`, etc.)
is unchanged.

---

## Testing Strategy

### Unit tests (all in `tests/unit/`)

**Profit target — `test_engine_profit_target.py`**
- Position hits target on bar.high → EXIT intent emitted with `reason="profit_target"`
- Position does NOT hit target → no EXIT intent
- Same-bar stop-and-target: stop takes priority (conservative path)
- `ENABLE_PROFIT_TARGET=false` → no EXIT even when bar.high ≥ target
- Extended hours: profit target check skipped

**Expectancy — `test_report_expectancy.py`**
- 100% win rate → expectancy = avg_win_pct
- 0% win rate → expectancy = avg_loss_pct
- 50% win/loss → correct weighted average
- Zero-trade report → expectancy = None

**Trend filter debounce — `test_engine_trend_filter_debounce.py`**
- Lookback=1: single day below SMA triggers exit (matches current behaviour)
- Lookback=2: one day below SMA → no exit; two consecutive days → exit
- Lookback=2: one day below, one above → no exit
- Insufficient history → no exit (hold)

### No migrations needed

No new Postgres columns or tables. `exit_reason` is a string field in the audit event
record and in the in-memory report; no schema changes required.

---

## Deployment Notes

All three features are gated by env vars defaulting to existing behaviour:

| Var | Default | Effect |
|---|---|---|
| `ENABLE_PROFIT_TARGET` | `false` | No change until operator enables |
| `PROFIT_TARGET_R` | `2.0` | Ready when feature is enabled |
| `TREND_FILTER_EXIT_LOOKBACK_DAYS` | `1` | Identical to current behaviour |

To enable profit targets after deploying:
```
ENABLE_PROFIT_TARGET=true
PROFIT_TARGET_R=2.0   # or tune to strategy
```

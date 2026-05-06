# Design: Open Positions Totals Row + 5% Max Stop Cap

Date: 2026-05-06

## Problem

Two independent improvements to the dashboard and trading engine:

1. **Totals row** — the Open Positions table has no aggregate row, making it impossible to see portfolio-level P&L or risk at a glance.
2. **Stop % cap** — non-breakout strategies (ORB, bull_flag) place stops at structural levels (opening range low, flag low) that can be 5–11% below entry. The user wants a hard cap of 5% stop distance from entry price on all positions.

---

## Feature 1: Open Positions Totals Row

### What is shown

A `<tfoot>` row at the bottom of the Open Positions table with these aggregates:

| Column | Aggregate |
|---|---|
| SYMBOL | "TOTAL" label |
| STRATEGY | — |
| QTY | sum |
| ENTRY | — |
| INIT STOP | — |
| STOP | — |
| STOP % | value-weighted average (weight = init_val per position) |
| STOP MOVED | — |
| RISK $ | sum |
| OPENED | — |
| UPDATED | — |
| LAST | — |
| INIT VAL | sum |
| CURR VAL | sum |
| UNREAL P&L $ | sum (red/green colored) |
| UNREAL P&L % | value-weighted average (weight = init_val per position) |

### Weighted average formula

```
weighted_avg_stop_pct  = sum(stop_pct_i  * init_val_i) / sum(init_val_i)
weighted_avg_upnl_pct  = sum(upnl_pct_i  * init_val_i) / sum(init_val_i)
```

This is computed inside the Jinja2 template using a loop accumulator, exactly matching the per-row calculations already present.

### Implementation scope

- **Template only** — `src/alpaca_bot/web/templates/dashboard.html`
- No Python changes needed; all required data (`snapshot.positions`, `snapshot.latest_prices`) is already in context.
- Only rendered when at least one position exists (same guard as the existing `{% else %}` empty-state row).

---

## Feature 2: 5% Max Stop Distance Cap

### Intent

No position — new or existing — may have a stop price more than 5% below its entry price. This cap is enforced in the trading engine, so it applies to all strategies uniformly. The cap is configurable via env var so it can be tightened or loosened without a code deploy.

### New setting

```
MAX_STOP_PCT   (float, default 0.05)
```

Validated: `0 < MAX_STOP_PCT <= 0.50`. Added to `Settings` and included in `Settings.to_env_dict()`.

### Enforcement: new entries

In `evaluate_cycle()`, after the `calculate_position_size` call and before appending to `entry_candidates`, the initial stop price for equity entries is clamped:

```python
cap_stop = round(signal.limit_price * (1 - settings.max_stop_pct), 2)
effective_initial_stop = max(signal.initial_stop_price, cap_stop)
```

`effective_initial_stop` is what flows into `CycleIntent.initial_stop_price` (and ultimately `PositionRecord.stop_price` at fill time). `signal.stop_price` (the stop-limit entry order trigger) is unchanged.

Options entries (`is_option=True`) are excluded — their `initial_stop_price` is a placeholder (0.01) and the cap does not apply.

### Enforcement: existing positions

Each cycle, after the trailing stop block in `evaluate_cycle()`, a cap-up pass runs over all open positions:

```python
cap_stop = round(position.entry_price * (1 - settings.max_stop_pct), 2)
if position.stop_price < cap_stop:
    # Emit UPDATE_STOP intent to move stop to cap_stop
```

The existing regression guard (`new_stop > position.stop_price`) already prevents moving stops down — the cap-up intent is consistent with this invariant. The UPDATE_STOP intent is handled by `execute_cycle_intents()`, which calls the broker directly (same path as trailing stop updates). An `AuditEvent` is appended with `event_type="stop_cap_applied"`.

### Interaction with trailing stops

Trailing stop logic runs first, cap-up logic runs second. Both only ever increase the stop price, so they compose safely. If trailing already moved the stop above the cap level, the cap-up check is a no-op.

### What happens to current open positions

On the next cycle after this change is deployed, positions whose stop is more than 5% below entry will have their stops moved up automatically. Using the live data from the screenshot:

| Symbol | Current stop% | New stop (5% cap) | Change |
|---|---|---|---|
| ACHR | 7.49% | $5.83 | +$0.15 |
| PL | 8.96% | $35.82 | +$1.49 |
| RGTI | 9.67% | $18.37 | +$0.90 |
| RSI | 5.40% | $25.68 | +$0.11 |
| SMCI | 10.97% | $31.09 | +$3.95 |
| SMR | 9.64% | $12.03 | +$0.59 |
| UAMY | 10.43% | $11.20 | +$0.64 |

Positions already within 5% (AAPL, BVS, CLOV, SATL, TDC) are unchanged.

### No display-only changes needed

Because the engine enforces the cap upstream, the dashboard `stop_dist_pct` formula is unchanged. Stop% values will naturally be ≤ 5% for all positions after the cap is applied.

---

## Out of scope

- Changing any strategy's stop-placement logic (ATR buffer, `breakout_stop_buffer_pct`, `atr_stop_multiplier`)
- Retroactive changes to closed trades or the audit log
- Per-strategy cap values (single global cap for now)
- Any new database columns or migrations

---
title: Per-Position Profit Trailing Stop
date: 2026-05-06
status: approved
---

# Per-Position Profit Trailing Stop

## Goal

Protect open-position profits from late-day sell-offs and sudden downturns by ratcheting the stop up to a fixed percentage of the session high. Once the trail candidate exceeds the existing stop, the stop is raised — it never decreases.

## Scope

Pure engine change: new logic inside `evaluate_cycle()` in `core/engine.py`. No database migration, no new domain types. Feature is gated behind a flag and disabled by default.

---

## Section 1 — Settings

Two new fields on the `Settings` frozen dataclass in `src/alpaca_bot/config/__init__.py`:

```python
enable_profit_trail: bool = False
profit_trail_pct: float = 0.95
```

`from_env()` parses two new env vars:

```python
enable_profit_trail = _bool(os.environ.get("ENABLE_PROFIT_TRAIL", "false"))
profit_trail_pct    = float(os.environ.get("PROFIT_TRAIL_PCT", "0.95"))
```

`validate()` adds a range check:

```python
if not (0 < self.profit_trail_pct < 1.0):
    raise ValueError(f"PROFIT_TRAIL_PCT must be in (0, 1); got {self.profit_trail_pct}")
```

Pattern mirrors existing feature flags: `enable_trend_filter_exit`, `enable_vwap_breakdown_exit`.

---

## Section 2 — Engine Logic

### Location

New block inside the `for position in open_positions` loop in `evaluate_cycle()`, placed **after** the existing ATR trailing stop block (which ends at the `UPDATE_STOP` append near line 228) and **before** the cap-up pass.

### Guard conditions (skip if any are true)

- `not settings.enable_profit_trail`
- `is_extended` — same guard as the existing ATR trail block
- No intraday bars for this symbol
- No today-session bars (same `session_date` filter already used by the VWAP breakdown exit)

### Computation

```python
session_date = now.astimezone(settings.market_timezone).date()
today_bars = [b for b in bars if b.timestamp.astimezone(settings.market_timezone).date() == session_date]
if not today_bars:
    continue  # (guard above)

today_high = max(b.high for b in today_bars)
trail_candidate = round(today_high * settings.profit_trail_pct, 2)

# Determine effective current stop (use already-emitted UPDATE_STOP if any)
effective_stop = emitted_update_stops.get(position.symbol, position.stop_price)

if trail_candidate > effective_stop:
    intents.append(
        CycleIntent(
            intent_type=CycleIntentType.UPDATE_STOP,
            symbol=position.symbol,
            timestamp=now,
            stop_price=trail_candidate,
            strategy_name=strategy_name,
            reason="profit_trail",
        )
    )
    emitted_update_stops[position.symbol] = trail_candidate
```

### Activation rule

**Option 3** (no separate "profitable" gate): the trail fires whenever `trail_candidate > effective_stop`. The monotonic invariant subsumes the activation rule — the stop only ever moves up. The feature naturally starts protecting profits once the session high is high enough to push the trail above the current stop.

### Interaction with ATR trailing stop

Both blocks run independently; `emitted_update_stops` is the shared state. The profit trail block reads `emitted_update_stops` to see if ATR already moved the stop up, and only emits if it would raise it further. Result: highest stop wins. No duplicate intents per symbol.

The existing cap-up pass (which also reads `emitted_update_stops`) runs after both blocks and continues to enforce `MAX_STOP_PCT`.

---

## Section 3 — Tests

Eight new unit tests in `tests/unit/test_cycle_engine.py`. All use `profit_trail_pct=0.90` (not the default 0.95) so hardcoded constant bugs are detectable.

| # | Scenario | Key fixture values | Expected outcome |
|---|---|---|---|
| 1 | Trail > current_stop → emits | entry=$10, stop=$9.00, today_high=$12, pct=0.90 → trail=$10.80 | UPDATE_STOP at $10.80 |
| 2 | Trail < current_stop (stock down) → no emit | entry=$10, stop=$9.50, today_high=$9.80, ATR-block disabled, pct=0.90 → trail=$8.82 | 0 UPDATE_STOP intents |
| 3 | ATR stop > profit trail → emit ATR stop | ATR trail=$10.60, profit trail=$9.90 | UPDATE_STOP at $10.60 |
| 4 | Profit trail > ATR stop → emit profit trail | profit trail=$10.80, ATR trail=$10.40 | UPDATE_STOP at $10.80 |
| 5 | Monotonic invariant | entry=$10, existing_stop=$11.40, today_high=$11.50, pct=0.90 → trail=$10.35 | Any UPDATE_STOP emitted has new_stop ≥ $11.40 |
| 6 | Feature flag off | enable_profit_trail=False | No UPDATE_STOP |
| 7 | Trail below current_stop at boundary | entry=$10, stop=$9.50, today_high=$10.00, pct=0.90 → trail=$9.00 | No emit (trail < stop) |
| 8 | Gray zone: trail > stop, trail < entry | entry=$10, stop=$8.50, today_high=$10.50, pct=0.90 → trail=$9.45 | UPDATE_STOP at $9.45 — trail fires whenever it tightens the stop, even before it crosses entry |

**ATR-block isolation technique (tests 2, 3, 5):** set `trailing_stop_profit_trigger_r` to a very large value (e.g., 100) so the ATR block never activates; test only the profit trail behavior.

**Test 3 / 4 ATR interaction:** set `trailing_stop_profit_trigger_r=0` (triggers immediately) and choose today_high and ATR period values so the candidates point in the desired direction.

---

## Non-goals

- No portfolio-level daily profit lock (e.g., halt entries once +2% on the day).
- No change to option position handling (profit trail is equity-position-only by default since `is_extended` already skips those paths, and options do not have ongoing stop tracking in the current design).
- No new database columns or migrations.

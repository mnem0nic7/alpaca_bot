# Spec: Position Viability Checks

**Date:** 2026-05-01

---

## Problem

Open positions are currently only closed by four mechanisms:

1. The broker-held stop order filling at Alpaca
2. The trailing stop ratchet (moves stop up once 1R profit is reached)
3. EOD flatten at `FLATTEN_TIME`
4. Daily loss limit (`daily_loss_limit_pct`) breached

The engine never re-evaluates whether a position's original trade thesis still holds. A position entered on a strong uptrend day will be held until the stop fires or EOD — even if the trend has since reversed and the daily SMA filter that gated the original entry would now fail. Similarly, if price drops back below VWAP intraday (often signalling that buyers are no longer in control), the bot holds until the stop fires.

This creates a risk gap: the broker stop is the only floor, and it only triggers at the stop price level. Mid-session trend reversals or structural breakdowns that don't reach the stop are invisible to the engine.

---

## Scope

Add two viability checks to `evaluate_cycle()`:

### Check 1: Trend Filter Reversal Exit

**What:** Re-evaluate `daily_trend_filter_passes()` for each open position every cycle. If it returns `False` (latest completed daily close has dropped below the daily SMA), emit an `EXIT` intent with `reason="viability_trend_filter_failed"`.

**Why:** The daily SMA trend filter gates all entries. A position entered when price was above the 20-day SMA (or whichever `daily_sma_period` is set to) should arguably be exited when that same filter now fails — the macro trend has reversed.

**Safety guard:** Only apply the check when `len(daily_bars) >= settings.daily_sma_period + 1`. If daily bar data is insufficient (e.g., fetch failed), skip silently — never exit on missing data.

**Controlled by:** `ENABLE_TREND_FILTER_EXIT` env var → `Settings.enable_trend_filter_exit: bool = False`. Default off — no behavioral change for existing deployments.

### Check 2: VWAP Breakdown Exit

**What:** Each cycle, compute VWAP from today's intraday bars for each open position's symbol. If the latest bar's close is below VWAP, emit an `EXIT` intent with `reason="viability_vwap_breakdown"`.

**Why:** VWAP is the volume-weighted average price of the current session. When price closes a 15-minute bar below VWAP, it signals that sellers have reclaimed the session's centre of gravity — a bearish structural shift applicable to all long positions, not just VWAP-specific strategies.

**Safety guard:** Only apply when there are today-dated intraday bars available (`today_bars` non-empty) and `calculate_vwap(today_bars)` returns non-None. Filter "today" using `now.astimezone(settings.market_timezone).date()`.

**Controlled by:** `ENABLE_VWAP_BREAKDOWN_EXIT` env var → `Settings.enable_vwap_breakdown_exit: bool = False`. Default off.

---

## Out of Scope (deferred)

- **Strategy-specific exit evaluators** (a `StrategyExitEvaluator` protocol with per-strategy `should_exit_*` functions) — valuable but complex; these two global checks cover the main cases.
- **Close-below-stop-price exit** — if price closes below the broker stop price, the broker stop should have already fired. A redundant engine-side exit could race with the stop fill. Deferred.
- **Any new DB migration** — no new columns or tables needed.

---

## Architecture

### Where checks are inserted in `evaluate_cycle()`

Current per-position loop structure:
```
for position in open_positions:
    A. EOD flatten → if fires, emit EXIT, continue
    B. Stale bar guard → if stale, continue (no exit, just skip)
    C. Extended hours guard → if extended, continue (skip trailing stop)
    D. Trailing stop ratchet
```

New structure:
```
for position in open_positions:
    A. EOD flatten → if fires, emit EXIT, continue
    B. Stale bar guard → if stale, continue
    C. [NEW] Viability checks → if fires, emit EXIT, continue
    D. Extended hours guard → if extended, continue (skip trailing stop)
    E. Trailing stop ratchet
```

Placement rationale:
- After A (EOD flatten): avoids double-emitting EXIT for the same position
- After B (stale bar guard): ensures we have fresh data before evaluating VWAP
- Before D (extended hours guard): viability is valid during extended hours too; the extended-hours guard only blocks trailing stop updates

### `evaluate_cycle()` remains pure

Both checks use data already passed in (`intraday_bars_by_symbol`, `daily_bars_by_symbol`, `settings`, `now`). No I/O is introduced.

### Audit trail

The existing `cycle_intent_executed` audit event in `_execute_exit()` already propagates `reason` from the intent to its payload. New reason strings appear in the audit log automatically with no code changes to `cycle_intent_execution.py`.

### No schema changes

The `CycleIntent.reason: str | None` field already exists. The `cycle_intent_executed` audit event already includes `reason` in its payload.

---

## Settings

Two new fields added to `Settings` (after `enable_live_trading`, near the boolean flags):

```python
enable_trend_filter_exit: bool = False
enable_vwap_breakdown_exit: bool = False
```

`from_env()` additions:
```python
enable_trend_filter_exit=_parse_bool("ENABLE_TREND_FILTER_EXIT", values.get("ENABLE_TREND_FILTER_EXIT", "false")),
enable_vwap_breakdown_exit=_parse_bool("ENABLE_VWAP_BREAKDOWN_EXIT", values.get("ENABLE_VWAP_BREAKDOWN_EXIT", "false")),
```

No `validate()` changes needed — booleans have no range constraints.

---

## New EXIT Reason Strings

| `CycleIntent.reason` | Trigger condition |
|---|---|
| `"viability_trend_filter_failed"` | Daily SMA trend filter fails for a held position |
| `"viability_vwap_breakdown"` | Latest intraday bar closes below today's VWAP |

These appear in `cycle_intent_executed` audit event payloads under `"reason"`, alongside the existing `"eod_flatten"` and `"loss_limit_flatten"` values.

---

## Files to Modify

| File | Change |
|---|---|
| `src/alpaca_bot/config/__init__.py` | Add 2 Settings fields + from_env() + no validate() change |
| `src/alpaca_bot/core/engine.py` | Add viability check block in per-position loop |

## Files to Create

| File | Contents |
|---|---|
| `tests/unit/test_position_viability.py` | All viability check tests (engine unit tests) |

## Files Unchanged

- `src/alpaca_bot/runtime/cycle_intent_execution.py` — EXIT execution already handles any reason string
- `src/alpaca_bot/storage/` — no schema changes
- All strategy files — viability checks are global, not per-strategy

---

## Test Coverage Required

For each check, tests must cover:
- Check fires when all conditions met → EXIT intent emitted with correct reason
- Check does NOT fire when disabled (flag off) → no EXIT intent
- Check does NOT fire when data is insufficient (guard test) → no EXIT intent
- Check does NOT fire when condition is not met (e.g., trend filter passes, or close >= VWAP)
- Check does NOT double-emit when EOD flatten already fired for the same position

All tests use the existing fake-callables pattern (no mocks). `make_settings()` local helper with `**overrides`. Bars built directly from `Bar(...)` constructor.

---

## Safety Properties

- **Default off:** Both checks are `False` by default. Zero behavioral change for existing deployments.
- **No I/O inside engine:** evaluate_cycle() remains a pure function.
- **Data guard:** Neither check fires on missing/insufficient data. Exit-on-missing-data would be a false positive.
- **No new order types:** Viability exits use the same market/limit exit path as EOD flatten.
- **Paper/live symmetry:** Settings flags apply equally in both modes. `ENABLE_LIVE_TRADING=false` gate is unaffected.
- **No race condition:** EXIT deduplication in `_execute_exit()` (checks for active exit order) already prevents double-submission.

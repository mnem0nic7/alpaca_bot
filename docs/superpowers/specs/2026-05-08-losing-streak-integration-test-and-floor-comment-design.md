# Losing-Streak Integration Test + Floor Comment — Design Spec

## Problem

Two follow-up items from the confidence-driven capital deployment feature review:

1. **Missing integration test**: `compute_losing_day_streaks` is unit-tested in isolation, but the full supervisor path — set-management (`_strategy_streak_excluded`), audit event transitions, and propagation into `entries_disabled_strategies` — has no test. A regression here could silently allow an excluded strategy to keep submitting entries.

2. **Undocumented timing**: `_check_and_update_floor_triggers` updates the confidence floor in the DB, but the floor value used for the current cycle was loaded earlier (line 362). A future developer could reasonably read this as a bug. One comment pins the intentional design.

## Scope

Two additive changes, no schema or API surface changes:

- `tests/unit/test_supervisor_weights.py` — 3 new tests
- `src/alpaca_bot/runtime/supervisor.py` — 1 comment line

## Design

### Integration Tests (3 tests in `test_supervisor_weights.py`)

All three tests use the existing `_make_supervisor` factory and `_RecordingOrderStore(pnl_rows=...)`. The `order_dispatcher` lambda is replaced with a capturing version to inspect `blocked_strategy_names`.

**PnL row shape** (already used by `compute_losing_day_streaks`):
```python
{"strategy_name": "breakout", "exit_date": date(2026, 4, 29), "pnl": -50.0}
```

**Settings override**: `LOSING_STREAK_N=2` to keep test data small.

---

**Test 1: `test_losing_streak_exclusion_emits_excluded_audit_event`**

Setup:
- `LOSING_STREAK_N=2`
- `_session_sharpes = {"breakout": 2.0}` (pre-seeded so weight computation is skipped)
- `_session_capital_weights = {"breakout": 1.0}` (pre-seeded)
- `_session_equity_baseline = {SESSION_DATE: 10_000.0}` (pre-seeded)
- `pnl_rows`: 2 consecutive losing days for "breakout" (last 2 days before session date)
- `_strategy_streak_excluded = set()` (default from `__init__`)

Run `supervisor.run_cycle_once(now=lambda: _NOW)`.

Assert:
- Audit store contains exactly one `strategy_confidence_excluded` event
- Event payload `strategy_name == "breakout"`
- `supervisor._strategy_streak_excluded == {"breakout"}`

---

**Test 2: `test_losing_streak_excluded_strategy_blocked_from_dispatch`**

Same setup as Test 1. Replace `order_dispatcher` with a callable that captures its kwargs.

Assert:
- `"breakout"` is in `captured_kwargs["blocked_strategy_names"]`

---

**Test 3: `test_losing_streak_restoration_emits_restored_audit_event`**

Setup:
- `LOSING_STREAK_N=2`
- Same pre-seeded session state
- `_strategy_streak_excluded = {"breakout"}` (pre-seeded — simulates strategy was previously excluded)
- `pnl_rows`: 2 losing days, then a winning day most recently (streak broken)

Run `supervisor.run_cycle_once(now=lambda: _NOW)`.

Assert:
- Audit store contains exactly one `strategy_confidence_restored` event
- Event payload `strategy_name == "breakout"`
- `supervisor._strategy_streak_excluded == set()`
- No `strategy_confidence_excluded` event fires (not newly excluded)

---

### Comment (1 line in `supervisor.py`)

Location: immediately before the `_check_and_update_floor_triggers(...)` call (currently line 662).

```python
# Floor changes written here take effect next cycle; confidence_floor was loaded above.
self._check_and_update_floor_triggers(
    current_equity=account.equity,
    now=timestamp,
    daily_bars_for_vol=_regime_bars_for_vol,
)
```

## Financial Safety

No financial-safety impact. Tests are additive. Comment is read-only. No order submission, position sizing, or stop placement is modified.

## Testing

- 3 new unit tests in `tests/unit/test_supervisor_weights.py`
- No migration, no new env vars, no schema changes

## Out of Scope

- Testing `_check_and_update_floor_triggers` itself (already covered in `test_confidence_floor_triggers.py`)
- Testing `compute_losing_day_streaks` in isolation (already covered in `test_confidence_floor_triggers.py`)

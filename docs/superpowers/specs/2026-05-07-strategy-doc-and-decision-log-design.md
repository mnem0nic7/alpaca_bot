# Design: STRATEGY.md + Trade Decision Logging

**Date:** 2026-05-07  
**Status:** Approved  
**Deliverables:** Two independent but complementary artifacts — a static strategy reference doc and a queryable per-trade decision log in Postgres.

---

## Problem Statement

The system makes ~200+ trade decisions per day but there is no:
1. Human-readable reference explaining *why* each rule exists and what parameters govern it.
2. Queryable record of *why* each specific candidate was accepted or rejected — making post-trade analysis impossible.

Without these, tuning is guesswork and debugging a missed entry requires reading source code.

---

## Deliverable 1: STRATEGY.md

A root-level markdown document (alongside `README.md`) that is the canonical reference for all trading logic. It covers:

- The 11 active strategies and their entry signal definitions
- Universal pre-filter logic (trend, session time, volume, regime, news, spread, bar age, already-traded)
- Stop placement logic (ATR-based initial stop, trailing stop activation, profit trail, stop cap)
- Exit logic (trailing stop hit, flatten time, daily loss limit, extended hours flatten)
- Risk/sizing math (risk budget → qty formula, position/exposure caps)
- Strategy weighting regime (Sharpe-proportional, min/max clip, fallback to equal)
- Extended hours behaviour
- Parameter cross-reference table: every configurable setting, its default, and WHY that value was chosen

**Source of truth:** Derived entirely from reading the current codebase. Not aspirational — reflects what is live.

---

## Deliverable 2: `decision_log` Table

### Purpose

For every entry candidate evaluated in `evaluate_cycle()`, record:
- Which strategy was evaluated, which symbol, at what time
- All filter pass/fail results (as JSONB so schema can evolve)
- Signal values that were computed (breakout level, ATR, relative volume, bar close)
- Risk sizing inputs and outputs
- Whether the candidate was accepted or rejected, and at which stage

Both **accepted** and **rejected** candidates are logged — rejections are the most valuable records for learning.

### New Domain Type: `DecisionRecord`

```python
@dataclass(frozen=True)
class DecisionRecord:
    cycle_at: datetime
    symbol: str
    strategy_name: str
    trading_mode: str            # "paper" | "live"
    strategy_version: str
    decision: str                # "accepted" | "rejected" | "skipped_no_signal" | "skipped_existing_position" | "skipped_already_traded"
    reject_stage: str | None     # None if accepted; else: "pre_filter" | "signal_none" | "invalid_signal" | "sizing" | "capacity"
    reject_reason: str | None    # human-readable cause
    # Signal values (None if signal was never computed)
    entry_level: float | None
    signal_bar_close: float | None
    relative_volume: float | None
    atr: float | None
    # Sizing outputs (None if sizing was never reached)
    stop_price: float | None
    limit_price: float | None
    initial_stop_price: float | None
    quantity: float | None
    risk_per_share: float | None
    equity: float | None
    # Filter results (captured even on rejection)
    filter_results: dict          # e.g. {"trend": True, "session_time": True, "volume": False, "regime": True, "news": True, "spread": None}
```

`DecisionRecord` lives in `domain/` as a pure data type alongside `Bar`, `OpenPosition`, etc.

### Extending `CycleResult`

Add `decision_records: list[DecisionRecord]` to the `CycleResult` frozen dataclass. `evaluate_cycle()` populates this list as it evaluates each candidate — including rejected ones. This preserves the pure-function boundary (no I/O inside the engine).

### Database Table: `decision_log`

```sql
CREATE TABLE decision_log (
    id              BIGSERIAL PRIMARY KEY,
    cycle_at        TIMESTAMPTZ NOT NULL,
    symbol          TEXT        NOT NULL,
    strategy_name   TEXT        NOT NULL,
    trading_mode    TEXT        NOT NULL,
    strategy_version TEXT       NOT NULL,
    decision        TEXT        NOT NULL,      -- accepted | rejected | skipped_*
    reject_stage    TEXT,
    reject_reason   TEXT,
    entry_level     NUMERIC(12,4),
    signal_bar_close NUMERIC(12,4),
    relative_volume NUMERIC(8,4),
    atr             NUMERIC(12,4),
    stop_price      NUMERIC(12,4),
    limit_price     NUMERIC(12,4),
    initial_stop_price NUMERIC(12,4),
    quantity        NUMERIC(12,4),
    risk_per_share  NUMERIC(12,4),
    equity          NUMERIC(14,2),
    filter_results  JSONB
);

CREATE INDEX ON decision_log (cycle_at DESC);
CREATE INDEX ON decision_log (symbol, cycle_at DESC);
CREATE INDEX ON decision_log (strategy_name, decision);
```

Migration: `015_add_decision_log.sql`.

### Storage Layer: `DecisionLogStore`

New repository class in `storage/repositories.py`:

```python
class DecisionLogStore:
    def bulk_insert(self, records: list[DecisionRecord], conn) -> None: ...
```

Uses a single `executemany` call per cycle — typically 5–50 rows. At 8 symbols × 26 cycles × 252 days = ~52K rows/year; fully manageable.

### Write Path in `runtime/cycle.py`

After `evaluate_cycle()` returns, `run_cycle()` calls `decision_log_store.bulk_insert(result.decision_records, conn)` within the same transaction that writes entry orders. If the insert fails, the cycle continues — decision logging is best-effort (no trading correctness dependency).

### Instrumentation Points in `evaluate_cycle()`

1. **Pre-filter rejection** — symbol has open position, working order, already traded today, or `entries_disabled` → `decision="skipped_existing_position"` or `decision="skipped_already_traded"`
2. **Regime block** — global regime filter fails → `decision="rejected", reject_stage="pre_filter", reject_reason="regime_blocked"`
3. **News block** — news keyword match → `decision="rejected", reject_stage="pre_filter", reject_reason="news_blocked"`
4. **Spread block** — NBBO too wide → `decision="rejected", reject_stage="pre_filter", reject_reason="spread_too_wide"`
5. **No signal** — signal evaluator returns None → `decision="skipped_no_signal"`
6. **Invalid signal** — breakout level ≤ 0 or ATR ≤ 0 → `decision="rejected", reject_stage="invalid_signal"`
7. **Sizing failure** — qty ≤ 0 → `decision="rejected", reject_stage="sizing", reject_reason="qty_zero"`
8. **Capacity block** — max open positions or max exposure reached → `decision="rejected", reject_stage="capacity"`
9. **Accepted** — emits ENTRY intent → `decision="accepted"`

Filter results (the JSONB blob) are captured at the point where we have the most information, even for rejections that happen early.

---

## Architecture Fit

| Concern | Where |
|---|---|
| `DecisionRecord` type | `src/alpaca_bot/domain/decision_record.py` |
| `CycleResult.decision_records` | `src/alpaca_bot/core/engine.py` |
| Population logic | `evaluate_cycle()` at each rejection/acceptance point |
| `DecisionLogStore` | `src/alpaca_bot/storage/repositories.py` |
| Write call | `src/alpaca_bot/runtime/cycle.py` → `run_cycle()` |
| Migration | `migrations/015_add_decision_log.sql` |
| STRATEGY.md | `/workspace/alpaca_bot/STRATEGY.md` (repo root) |

---

## Out of Scope

- Dashboard UI for browsing decision log (future feature)
- Exit decision logging (this spec covers entry candidates only)
- Stop-update decision logging

These can be added later without schema changes (the `decision` column is a free-text enum extensible via new values).

---

## Testing Strategy

- Unit test: `test_evaluate_cycle_decision_records` — verify accepted and rejected records appear in `result.decision_records` with correct fields
- Unit test: `test_decision_log_store_bulk_insert` — verify SQL insert roundtrip
- Existing engine tests must continue to pass (no regressions — `decision_records` defaults to empty list)
- `DecisionRecord` is a frozen dataclass so it's trivially testable without any fakes

---

## Volume Estimate

~200 candidates/day × 252 trading days = ~50K rows/year. At the default 8-symbol config: ~52K rows/year. Minimal storage impact.

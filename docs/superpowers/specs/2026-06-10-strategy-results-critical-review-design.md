# Strategy & Results Critical Review — Design Spec

**Date:** 2026-06-10
**Trigger:** Operator request: "examine our trading strategy and results with a critical eye and make suggestions; paper trading, anything is testable."
**Method:** Direct queries against production Postgres (`deploy-postgres-1`), audit-event analysis, code tracing of the capacity, weighting, and confidence-floor paths.

---

## Part 1 — Findings (evidence-backed)

### F1. The bot has been effectively dead since 2026-05-25 (capacity starvation)

Zero equity entry submissions since May 25 (one stray submit on June 8). Root cause chain:

1. Nine stale short June-18 puts (opened 2026-05-22, `strategy_name='short_option'`, `stop_price=0`) sit in `positions` — imported from the broker by startup recovery.
2. `global_occupied_slots` counts each OCC symbol **and** its underlying (via `working_order_symbols`), plus stuck broker orders (21 `option_orders` rows frozen in `status='submitted'` for CRMD/ASAN/CXM dating to May 18).
3. Total occupied ≥ `MAX_OPEN_POSITIONS` (20) → `available_slots == 0` every cycle → every candidate rejected at `reject_stage='capacity'`.

Fixes for the underlying stale-OCC bugs exist at HEAD (`d679cd2` close stale OCC via EXIT intents, `5f7a7fb` option dispatch REGULAR-hours guard) but are **not deployed** — the supervisor container runs a ~2-week-old image.

### F2. decision_log is 32 GB and growing ~10–11M rows/day

When `available_slots == 0`, `evaluate_cycle()` writes one `capacity_full` DecisionRecord **per watchlist symbol (1,003) per strategy (~23) per 60-second cycle**. Since May 26 that produced 116.9M rows; lifetime, `capacity_full` accounts for 48M+ more. This will fill the disk and makes the funnel report useless (see F8).

### F3. Nine unmanaged naked short puts + a wrong-side close loop

The stale short puts have `stop_price=0` (no risk management). The supervisor tries to close them every cycle with **sell** orders (correct close for a short is buy-to-close); Alpaca rejects them — ~100 `option_order_dispatch_failed` events/day, 1,216 total, still firing today. `d679cd2` at HEAD addresses the close path but is undeployed.

### F4. exit_hard_failed retry storm

18,303 `exit_hard_failed` events May 14–23 (up to 3,014/day) — `submit_market_exit_retry_failed` on CMG with `insufficient qty available (held_for_orders: 400)`: the exit retried every cycle against qty already reserved by another order, with no dedup/backoff.

### F5. Honest per-strategy results: the whole stack is ~break-even-to-losing

Matched intraday round trips only (same symbol+strategy+day, buy qty == sell qty):

| Strategy | Round trips | P&L | Win rate | Avg/trade |
|---|---|---|---|---|
| breakout | 48 | +$51.26 | 42% | +$1.07 |
| failed_breakdown | 4 | −$21.72 | 25% | −$5.43 |
| vwap_reversion | 1 | −$65.45 | 0% | −$65.45 |
| bull_flag | 28 | −$110.96 | 57% | −$3.96 |
| orb | 55 | −$203.12 | 42% | −$3.69 |
| ema_pullback | 19 | −$203.72 | 26% | −$10.72 |
| bb_squeeze | 13 | −$234.84 | 8% | −$18.06 |
| vwap_cross | 22 | −$342.59 | 9% | −$15.57 |
| momentum | 41 | −$345.75 | 29% | −$8.43 |

Total: ≈ −$1,477 across 231 round trips. Note the trade sizes: average P&L magnitude is $1–$20 on ~$100k equity (RISK_PER_TRADE_PCT=0.0025 further scaled by confidence). At this size, results are statistically indistinguishable from noise, and any real-world slippage/borrow costs would dominate.

### F6. The Sharpe weighting runs on corrupt data

`strategy_weights` today: breakout Sharpe **5.85** (weight 0.474), orb 2.51 (0.316), all else floored at 0.01. That Sharpe is fiction:

- The weighting path (`_refresh_strategy_weights` → `OrderRepository.list_trade_pnl_by_strategy`) matches each exit fill to "the most recent entry fill ≤ exit time" with **no quantity consumption** (multiple exits can match the same entry) and **no same-session constraint**.
- Recovery/carryover liquidations are written to `orders` with default `strategy_name='breakout'`; the orders table shows 134 breakout sells vs 67 buys, +$137,651 of unmatched sell cash flow credited to breakout.
- Result: breakout's "Sharpe 5.85" is built from broker-position liquidations it never signaled.

This poisons everything downstream: weights, confidence ranking, nightly sweep comparisons, the weekly review.

### F7. The confidence floor is locked at 0.8 with no escape

Auto-raised to 0.80 on 2026-05-14 ("drawdown hysteresis 2.9%, vol 2.72%"). Clear requires drawdown < 2.5% of the equity high-watermark ($99,712), but equity sits ~2.9% below — and because the bot cannot trade (F1), equity can never climb back. A strategy below the floor gets `entries_disabled`, earns no trades, no Sharpe, and stays below the floor: a permanent lockout loop with no time-based override.

### F8. Funnel telemetry is one-note

In the entire 6-week history, `capacity_full` is the **only** reject reason ever recorded (48M pre-May-26, 117M after). News, spread, sector, regime, and sizing rejections never appear — capacity starvation short-circuits the funnel before per-symbol filters run. The funnel report cannot answer "which filter costs us the most entries."

### F9. Capacity accounting double-counts options

Each short option consumes two slots (OCC symbol as a position + its underlying as a working symbol). 9 option positions consumed ≥18 of 20 slots. Options and equities should not share one undifferentiated slot pool.

---

## Part 2 — Recommendations

### Operator runbook (no code; surfaced to operator)

1. **Deploy HEAD** (`./scripts/deploy.sh /etc/alpaca_bot/alpaca-bot.env`) — picks up d679cd2/5f7a7fb plus the circuit breaker + notification stack. Add `OPTION_STRATEGY_MAX_ROLLING_LOSS_USD=500`, `OPTION_STRATEGY_ROLLING_LOSS_DAYS=7` to the env file.
2. **Clear the stale state:** flatten the 9 short puts (buy-to-close at broker or let the fixed close path do it post-deploy); mark the 21 stuck `submitted` option_orders rows failed.
3. **One-time purge:** `DELETE FROM decision_log WHERE reject_reason='capacity_full'` then `VACUUM FULL decision_log` (recovers ~32 GB; requires brief table lock — do off-hours).
4. **Reset the confidence floor** to the manual baseline (0.25) once trading resumes.
5. **Disable proven losers** for the next evaluation window via strategy flags: `bb_squeeze`, `vwap_cross`, `vwap_reversion`, `failed_breakdown` (win rates 0–25%, all negative).
6. **Raise paper sizing for measurability:** RISK_PER_TRADE_PCT 0.0025 → 0.01 and MAX_POSITION_PCT 0.015 → 0.05 in paper, so per-trade P&L rises above noise and strategy comparison becomes statistically meaningful.
7. **Re-run the nightly sweep after the data fix** and compare recommended parameters against live settings for breakout/orb.

### Code changes (committed scope of this spec)

**S1 — Stop the capacity flood (engine).** When `available_slots == 0`, `evaluate_cycle()` writes **one** aggregate DecisionRecord per strategy per cycle (sentinel `symbol='_capacity_'`, `filter_results={"blocked_symbol_count": N}`) instead of one per symbol. The per-candidate exposure-stage records (few per cycle) are unchanged. Funnel queries that count capacity rejections weight the aggregate row by `blocked_symbol_count`.

**S2 — Trustworthy P&L attribution (storage).** `OrderRepository.list_trade_pnl_by_strategy` requires the matched entry fill to be on the **same session date** (market timezone) as the exit. All equity strategies flatten at 15:45 ET, so genuine round trips are intraday; recovery/carryover liquidations have no same-day entry and drop out. This cleans the input to strategy weights, losing-streak checks, weekly review, and session eval.

**S3 — Confidence floor max-age escape (runtime + storage).** Track when the floor was last auto-raised (`floor_raised_at`, new column, nullable, migration). If a system-raised floor has persisted longer than `FLOOR_AUTO_RAISE_MAX_AGE_DAYS` (new Settings field, default 7, validated > 0) without a fresh raise trigger (hysteresis keep-alive does **not** reset the clock), clear to the manual baseline and emit `confidence_floor_auto_cleared` with reason `max age exceeded`. Manual floors are never auto-cleared.

**S4 — decision_log retention (admin + nightly).** New `DecisionLogStore.prune(older_than_days)` and admin command `alpaca-bot-admin prune-decision-log --keep-days N` (default 30). The nightly pipeline calls prune after its report so the table stops growing unbounded. Pruning appends an AuditEvent with the deleted row count.

### Explicitly out of scope

- F9 (separate option/equity capacity budgets) — deferred; after the stale-state cleanup and S1–S4, re-evaluate whether double-counting still binds in practice.
- F4 (exit retry backoff) — the May storm was a symptom of the stale-position mess; revisit if it recurs post-deploy.
- Any change to signal logic or strategy parameters — until S2 produces ≥2 weeks of clean attribution data, parameter tuning would be fitting to noise.

---

## Part 3 — Design notes

- **Pure engine boundary:** S1 changes only what DecisionRecords `evaluate_cycle()` returns — still pure, no I/O.
- **Audit trail:** S3 floor clear and S4 prune both append AuditEvents.
- **Paper/live parity:** all four changes are mode-agnostic; no live-only or paper-only branches.
- **Migration safety:** S3's column is nullable with no backfill; reversible. S4 deletes only rows older than the cutoff, inside a single transaction.
- **Testing:** each change gets unit tests using the project DI pattern (fake stores, no mocks): S1 asserts one aggregate record and intact exposure-stage records; S2 asserts cross-day exits are excluded and same-day round trips survive; S3 asserts clock semantics (raise sets it, hysteresis does not, expiry clears); S4 asserts cutoff math and audit event.

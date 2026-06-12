# Replay Harness Integrity, Honest Re-evaluation, and P&L Response — Design

**Date:** 2026-06-12
**Request:** "do a full code review, evaluate trading strategy, and maximize P&L"
**Origin:** Recommendation #1 of `docs/strategy-audit/2026-06-11-contrarian-strategy-evaluation.md` (commit bc600d5). Strategy evaluation and P&L maximization are gated on fixing the two replay-harness defects found there; the code review is independent and runs in parallel.

## Problem

Two defects make every past backtest/sweep result untrustworthy:

1. **Symbol-universe mismatch.** `evaluate_cycle()` iterates `settings.symbols` (core/engine.py:686). `ReplayRunner.run()` never passes the scenario's symbol through the engine's existing `symbols` override parameter (core/engine.py:109). Scenarios for symbols outside the 8-name watchlist produce zero decisions — 991 of 999 nightly scenarios were silently never evaluated.
2. **Look-ahead, scenario-constant daily trend gate.** runner.py:104 passes the FULL daily series every bar. End-anchored evaluators (16 of 23) therefore compute their trend filter at the scenario's final date — a constant for the whole scenario, evaluated with future data. On the nightly store's end date all 8 watchlist symbols were below SMA20, structurally zeroing every trend-gated strategy.

**Live convention (verified):** the supervisor fetches daily bars with `end = midnight ET of session_date` (runtime/supervisor.py:677-688; test `test_supervisor_passes_midnight_of_session_date_as_daily_bars_end`) — the series ends at the prior completed day. Replay must reproduce that shape.

## Phase 1 — Harness fix (code)

Single file: `src/alpaca_bot/replay/runner.py`, inside `ReplayRunner.run()`.

1. **Symbols override:** pass `symbols=(scenario.symbol,)` to `evaluate_cycle()`.
2. **Point-in-time daily slicing:** per intraday bar, compute `day = session_day(bar.timestamp, self.settings)` (already imported). When the day changes from the previous bar's, recompute
   `daily_slice = [b for b in sorted_daily if b.timestamp.astimezone(self.settings.market_timezone).date() < day]`
   where `sorted_daily = sorted(scenario.daily_bars, key=lambda b: b.timestamp)` is computed once per run. Pass `{bar.symbol: daily_slice}` as `daily_bars_by_symbol`. Cache by day so the filter runs once per session day, not per bar.

Design consequences (intentional):
- Evaluators with their own point-in-time defenses (momentum, gap_and_go, failed_breakdown date filters; high_watermark/bear_low_watermark `[:-1]`) see those defenses become no-ops or one-day-stale exactly as they are in live. No evaluator file changes.
- Early scenario days have short daily history → trend filters return False until `daily_sma_period + 1` prior days exist. This is correct warm-up behavior, not a bug.
- `tuning/sweep.py` (lines 248, 305, 360), `replay/audit.py`, `nightly/`, and all CLIs use this same runner — fixed for free.
- ATR from daily bars (`calculate_atr`) becomes point-in-time for all strategies, so position sizes/stops in replay change too. Expected.

**Timezone choice:** `astimezone(market_timezone).date()` rather than bare `.date()` — Alpaca daily bars are midnight-ET-anchored (04:00/05:00 UTC) so both agree today, but the explicit conversion is robust to any future bar source.

**Test plan (TDD, fakes, no mocks):**
- New test: a scenario whose symbol is NOT in `settings.symbols` produces decision records / entry events (regression for defect 1).
- New test: a scenario where the symbol is above its SMA in the first half and below at the end produces entries in the first half (regression for defect 2 — old code produced zero).
- New test: daily slice passed to the evaluator on day D contains no bar with session date ≥ D (assert via a capturing fake evaluator).
- Existing replay-runner/sweep/audit tests: expected-trade fixtures pinned to the old full-series gate may legitimately change; review each failure and re-pin only after confirming the new value is the point-in-time-correct one.

**Risk: none to live trading.** Replay-only file; `evaluate_cycle()` stays pure; no env vars, no migrations, no order paths.

## Phase 2 — Honest re-evaluation (ops, gated on Phase 1)

- Re-run `alpaca-bot-backtest audit --scenario-dir /var/lib/alpaca-bot/nightly/scenarios --slippage-bps 5` (all 11 equity strategies, now genuinely 999 scenarios; momentum gains 991 new symbol-years, so even its verdict can move).
- Re-run the sweep (`alpaca-bot-sweep`) for any strategy whose audit verdict is not negative-edge, to find parameters worth OOS-testing.
- Write `docs/strategy-audit/2026-06-12-honest-reevaluation.md`: verdict table, deltas vs the 2026-06-11 audit, and explicit comparison against the void R7 results.

## Phase 3 — P&L response (evidence-gated config; no invented parameters)

Decision rules, applied only to Phase 2 outputs:
- **negative-edge** at 5 bps → strategy stays/becomes disabled in the live (paper) config.
- **positive-edge** → candidate parameters flow through the existing nightly OOS gate → `candidate.env` → apply via the existing candidate-apply flow. No bypass.
- **no-evidence / insufficient-data** → unchanged; flagged for more data, not enabled.
- All config changes append an AuditEvent via the existing flows; `ENABLE_LIVE_TRADING=false` and `TRADING_MODE=paper` untouched.

"Maximize P&L" therefore means: stop strategies with measured negative edge, promote only strategies with statistically significant positive edge after costs, and tune only through the OOS-gated pipeline. No discretionary parameter twiddling.

## Phase 4 — Full code review (independent; parallel agents)

Parallel review agents over the trading-critical paths, per the project's reviewer/security-auditor mandates:
- Correctness review: `core/engine.py`, `risk/`, `runtime/order_dispatch.py`, `runtime/cycle_intent_execution.py`, `replay/` (post-fix).
- Security review: `execution/`, `config/`, `admin/`.
- Findings consolidated in `docs/reviews/2026-06-12-full-code-review.md`; high-severity items become their own plan-and-refine cycle — not fixed ad hoc inside this one.

## Approaches considered

- **A (chosen): runner-local fix.** One file, fixes all 23 evaluators, zero live-path risk, makes replay data shape identical to live.
- **B: per-evaluator point-in-time reconstruction** (momentum pattern everywhere). ~20 files of churn, touches the live signal path, redundant once A exists.
- **C: slice inside `evaluate_cycle()`.** Touches the shared pure engine; live already passes the correct shape, so this adds double-filtering risk for no benefit.

## Success criteria

- Phase 1: new regression tests pass; full suite green; an off-watchlist nightly scenario replays with nonzero decisions; trend gate varies within a scenario.
- Phase 2: audit table over genuinely-999 scenarios committed.
- Phase 3: every enable/disable/parameter change traceable to a Phase 2 verdict + OOS gate.
- Phase 4: review doc committed with severity-ranked findings.

# Cross-sectional / portfolio top-K replay — design spec

**Date:** 2026-06-16
**Status:** design (brainstorming output; feeds writing-plans)
**Type:** read-only diagnostic. No production config changed. Candidates only —
promotion stays exclusively through the nightly OOS gate (`alpaca-bot-nightly` →
`candidate.env`), never hand-applied.

## Problem

The profitability R&D has converged on **cross-sectional selectivity** as the
last untested mechanism for raising per-trade edge
(`docs/strategy-audit/2026-06-15-break-even-slippage.md`, commit fa67c2b). Single-
factor lever tuning is falsified (null, cbc082f); cost drag is the binding
constraint (`2026-06-12-honest-reevaluation.md`); bull_flag's break-even
≈ 3.99 bps/side leaves only ~1 bp of headroom over realistic execution cost —
too thin to rely on. Raising per-trade edge widens break-even and thickens the
margin, and the one lever not yet exercised is **ranking candidate signals across
many symbols each cycle and taking only the top-K, sized against shared portfolio
equity.**

This is currently **inexpressible** in the replay harness, but **not** because the
engine lacks the capability.

## Pivotal finding — the engine is already cross-sectional

`evaluate_cycle` is portfolio-aware today:

- [engine.py:996-1018](../../src/alpaca_bot/core/engine.py#L996-L1018) builds
  `entry_candidates` as `(momentum, relative_volume, CycleIntent)` across **all**
  symbols in `settings.symbols` (loop at line ~686).
- [engine.py:1020-1022](../../src/alpaca_bot/core/engine.py#L1020-L1022) sorts by
  `(-momentum, -relative_volume, symbol)` — the cross-sectional ranking.
- [engine.py:1023-1035](../../src/alpaca_bot/core/engine.py#L1023-L1035) selects up
  to `available_slots` (= `max_open_positions` − current open count) **and**
  enforces `max_portfolio_exposure_pct` via a running `current_exposure` sum.

The defect is entirely in the **harness**:
[runner.py:135](../../src/alpaca_bot/replay/runner.py#L135) passes
`symbols=(scenario.symbol,)` with single-symbol bar mappings, so
`entry_candidates` never holds more than one element — the ranking and the K-cap
are permanent no-ops. Each of the 999 scenarios also runs with its **own** fresh
`starting_equity`, so symbols never compete for capital.

**Consequence for scope:** we do not add ranking logic or a signal-strength
scalar. We add a new *runner* that feeds the existing pure engine the full multi-
symbol mappings against **one shared equity pool**, plus a pooled-trades adapter
that drops into the `pooled_trades_fn` parameter `run_audit` and
`run_break_even_sweep` already expose. `evaluate_cycle` stays pure and untouched.

## Goals

1. A `PortfolioReplayRunner` that replays **all symbols jointly** over the shared
   269-day window, calling the unchanged pure `evaluate_cycle` once per cycle with
   the full symbol set, a shared equity pool, and a real `global_open_count`, and
   emitting a pooled `list[ReplayTradeRecord]`.
2. A `_portfolio_pooled_trades(scenarios, settings, strategy_name)` adapter that
   matches `PooledTradesFn` exactly, so it is injectable into `run_audit` and
   `run_break_even_sweep` with **zero change to the audit objective**.
3. A CLI entry point to run the portfolio audit / break-even over
   `/var/lib/alpaca-bot/nightly/scenarios`.
4. A strategy-audit report comparing **single-symbol pooled baseline** vs
   **portfolio top-K** per-trade edge for bull_flag, swept over K
   (`max_open_positions ∈ {5,10,20}`), scored by the identical seeded-bootstrap CI
   + `classify_verdict` objective.

## Non-goals

- No change to `evaluate_cycle`, the audit objective, `classify_verdict`, the
  bootstrap, or `REPLAY_SLIPPAGE_BPS` production semantics.
- No new signal-strength field on `EntrySignal` (deferred; only justified if the
  diagnostic shows selectivity has promise).
- No re-backfill / new scenario format on disk. The per-symbol JSON store is
  reused as-is.
- No live-trading or production config change. Bot stays `close_only`,
  `TRADING_MODE=paper`, `ENABLE_LIVE_TRADING=false`.

## Design

### Data model — join existing per-symbol scenarios (Tension 1)

Verified: all 999 files in `/var/lib/alpaca-bot/nightly/scenarios` share an
**identical** session-day axis — 269 trading days, 2025-05-19 → 2026-06-12, 269
daily bars per symbol. Intraday bar counts vary per symbol (3,944–7,376) due to
halts/gaps, but every symbol covers the same session days. So a multi-symbol
"trading day" view is built **by joining the existing per-symbol scenarios on
timestamp — no re-backfill.**

`PortfolioReplayRunner.run(scenarios: Sequence[ReplayScenario])`:
- Index each scenario's pre-sorted intraday and daily bars by symbol.
- Build the **cycle timeline** = the sorted union of distinct intraday bar
  timestamps across all symbols (~27/day × 269 ≈ 7,200 cycles). This mirrors the
  live supervisor, which runs `evaluate_cycle` once per intraday cadence with all
  symbols.
- Maintain per-symbol **cursors** (monotonic indices) so point-in-time slicing is
  O(1) amortized per advance, never an O(n²) re-filter.

### Cycle loop (Tension 2 — ranking stays in the pure engine)

At each cycle timestamp `t`:

1. **Resolve fills/exits first**, per symbol that has a bar at `t`, reusing the
   single-symbol fill/stop/target/EOD mechanics (see "Reuse via extraction"
   below). Each realized exit debits/credits the **shared** equity pool and emits
   a `ReplayTradeRecord`.
2. **Assemble point-in-time slices for every symbol**: intraday bars up to `t`;
   daily bars with `astimezone(market_timezone).date() < session_day(t)` (the
   fe809c0 look-ahead fix, applied per symbol).
3. **Call `evaluate_cycle` once** with:
   - `intraday_bars_by_symbol` / `daily_bars_by_symbol` = the full multi-symbol
     point-in-time mappings,
   - `equity` = shared pool,
   - `open_positions` = all currently open portfolio positions,
   - `working_order_symbols` = symbols with a pending entry order,
   - `traded_symbols_today` = portfolio-wide set of `(symbol, session_day)`,
   - `global_open_count = len(open_positions)`,
   - `symbols` = the full tuple of scenario symbols (the one line the single-
     symbol runner gets wrong),
   - everything else mirrored from the single-symbol call contract
     ([runner.py:124-136](../../src/alpaca_bot/replay/runner.py#L124-L136)).
4. The engine returns ENTRY intents **already ranked and capped to K**
   (`available_slots`) and **already exposure-limited**. The runner places a
   working order per selected ENTRY for the symbol's next bar (same next-bar
   execution semantics as today), and applies UPDATE_STOP / EXIT intents per
   symbol.

Because ranking + K-cap + exposure-cap live in the engine, the runner is pure
bookkeeping (timeline, cursors, shared equity, per-symbol position/order state).

### Shared-equity sizing (Tension 4)

The portfolio runs **one** equity pool seeded at `AUDIT_STARTING_EQUITY`
(100,000), **not** 100k per symbol. `calculate_position_size`
([risk/sizing.py:8-44](../../src/alpaca_bot/risk/sizing.py#L8-L44)) already sizes
each entry against the passed `equity` with `risk_per_trade_pct`,
`max_position_pct`, and the optional `max_loss_per_trade_dollars` cap — no change
needed; the runner simply passes the shared pool. Aggregate exposure is bounded
two ways, both already enforced by the engine: `max_open_positions` × per-trade
`max_position_pct` (20 × 1.5% = 30%) is internally consistent with
`max_portfolio_exposure_pct` (0.30), and the engine's running `current_exposure`
check ([engine.py:1032](../../src/alpaca_bot/core/engine.py#L1032)) caps it
directly. Equity compounds across closed trades within the single portfolio run —
this is the behavioral difference from pooling 999 independent single-symbol
replays, and the reason selectivity can matter.

**K is a sweep parameter set via `dataclasses.replace(settings,
max_open_positions=K)`** — never a production config change.

### Ranking key (Tension 3)

Reuse the existing `(momentum = close/entry_level − 1, relative_volume)` sort. The
diagnostic measures whether the **existing** ranking, applied cross-sectionally,
raises per-trade edge. A bespoke signal-strength scalar is deferred — adding one
would alter `evaluate_cycle` behavior and is only justified if this diagnostic
shows selectivity has promise.

### Scoring reuse (Tension 5)

`_portfolio_pooled_trades(scenarios, settings, strategy_name) ->
list[ReplayTradeRecord]` matches `PooledTradesFn`
([audit.py:22-24](../../src/alpaca_bot/replay/audit.py#L22-L24)) exactly, but runs
**one** portfolio simulation over all scenarios instead of 999 independent ones.
It is injected via the existing `pooled_trades_fn` parameter of `run_audit`
([audit.py:72-124](../../src/alpaca_bot/replay/audit.py#L72-L124)) and
`run_break_even_sweep`
([break_even.py:80-123](../../src/alpaca_bot/replay/break_even.py#L80-L123)). The
bootstrap CI, `bootstrap_p_positive`, `MIN_SAMPLES`, and `classify_verdict` are
untouched, so portfolio results are scored by the **identical** objective and are
directly comparable to the single-symbol baseline.

### Reuse via extraction (DRY)

The per-symbol fill / stop-hit / profit-target / EOD-exit / `_slipped` mechanics
in `ReplayRunner` ([runner.py:66-76, 196-399](../../src/alpaca_bot/replay/runner.py#L196-L399))
are identical for a single lane of the portfolio. Extract them into **stateless
module-level helpers** (taking explicit position/equity/bar state and returning
the updated state + any `ReplayTradeRecord`), then have **both** `ReplayRunner`
and `PortfolioReplayRunner` call them. This keeps slippage and exit semantics
provably identical across the two runners and avoids a second copy of the fill
math. The extraction is behavior-preserving: the existing replay test suite must
stay green with zero fixture changes.

## Performance & memory

- Work is comparable to the existing single-symbol audit: that run evaluates every
  symbol at every bar across 999 scenarios (~7M symbol-bar evaluations); the
  portfolio run evaluates every symbol once per ~7,200 cycles (~7M symbol-
  evaluations). Same order of magnitude → expect roughly audit-scale wall-clock
  (~2 h/strategy/rung), governed by `evaluate_cycle`'s internal per-symbol cost.
- Memory: holding all 999 scenarios in RAM is the same ~2.4 GB peak measured for
  the break-even run. **Run one portfolio process at a time** (the server has
  ~6 GB free; a second concurrent 2.4 GB job risks the OOM-killer reaping
  postgres/supervisor). The CLI must not fan out multiple full-universe portfolio
  jobs concurrently.
- Cursor-based point-in-time slicing (not re-filtering) is mandatory to keep the
  per-cycle cost bounded.

## Data-quality edge cases

- **Stale/missing symbol at a cycle timestamp:** if a symbol has no bar at `t`
  (halt/gap), its point-in-time slice simply ends at its last available bar —
  mirroring live behavior where some symbols lag. No synthetic bars.
- **Per-symbol EOD:** each symbol's open position is flattened at its own session
  EOD (engine emits the EXIT intent on the last bar of that symbol's day), against
  the shared pool.
- **Re-entry guard:** `traded_symbols_today` is tracked portfolio-wide so a symbol
  that entered earlier in a session is not re-selected — same rule as today, now
  meaningful because multiple symbols compete.

## Deliverable report

`docs/strategy-audit/2026-06-16-cross-sectional-topk.md` comparing, for bull_flag:

| arm | equity model | selection | per-trade mean | ci_low | p | verdict |
|---|---|---|---|---|---|---|
| baseline | 100k per symbol | all signals | … | … | … | … |
| top-K=20 | 100k shared | top-20/cycle | … | … | … | … |
| top-K=10 | 100k shared | top-10/cycle | … | … | … | … |
| top-K=5 | 100k shared | top-5/cycle | … | … | … | … |

Tighter K yields fewer trades with (hypothesis) higher mean; the bootstrap CI
widens with the smaller sample, so `classify_verdict` honestly tests whether the
edge rises **enough** to clear `ci_low > 0` and `p < 0.05`. Run at the production
5 bps and across the break-even ladder so the cost headroom under selectivity is
directly readable. In-sample/descriptive; any promotion is exclusively via the
nightly OOS gate.

## Constraints honored

- `evaluate_cycle` stays pure; no I/O added to the engine.
- Intent → dispatch separation and audit-log-over-direct-state are irrelevant to
  this offline read-only path (no Postgres writes, no broker calls, no AuditEvent).
- `TRADING_MODE=paper`, `ENABLE_LIVE_TRADING=false`, bot stays `close_only`. No
  production config touched. `REPLAY_SLIPPAGE_BPS` production semantics unchanged —
  slippage is a sweep parameter via `dataclasses.replace` only. K likewise.
- Reuses existing audit/replay/stats infrastructure; the audit objective is
  unmodified.
- Commit trailer on every commit:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Open questions resolved during brainstorming

1. *Join existing scenarios without re-backfill?* **Yes** — identical 269-day axis
   across all 999 files.
2. *Where does top-K live?* **Already in the pure engine**; the runner just feeds
   it the full symbol set + shared equity.
3. *New ranking scalar?* **No** — reuse existing momentum/relative_volume.
4. *Shared-equity sizing?* **One 100k pool**; existing sizing + engine exposure cap
   suffice; K swept via `Settings`.
5. *Scoring comparability?* **Drop-in `PooledTradesFn`**; audit objective untouched.

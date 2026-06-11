# Contrarian Strategy Audit — Design

**Date:** 2026-06-11
**Status:** Approved (autonomous, per plan-and-refine mandate)
**Request:** "Evaluate all of our strategies with the contrarian eye of an expert trader and data analyst."

## Problem

We just learned our headline results were fiction (+$137k / Sharpe 5.85 from misattributed
recovery liquidations; honest P&L ≈ −$1,477). The S1–S4 fixes repaired *attribution*, but the
question remains: **are the evaluation methods themselves still fooling us?** Specifically:

1. **Costs:** ReplayRunner models zero slippage/commission. Entry fills at `max(open, stop)`,
   stops at `min(stop, open)`, profit targets at exact touch (`runner.py:287-301`) — fill-at-touch
   is optimistic for limit-style exits. Every sweep score, OOS gate decision, and nightly
   candidate.env is computed on frictionless fills.
2. **Significance:** No confidence intervals, bootstrap, or hypothesis tests anywhere. A strategy
   with 12 backtest trades and Sharpe 1.4 is indistinguishable from noise, but scores as a winner.
3. **Multiple comparisons:** Grid sweep tries ~48 combos/strategy × 999 scenarios; best-IS is an
   order statistic. The OOS gate (top-10, OOS ≥ 0.6×IS, OOS ≥ 0.2 absolute, `nightly/cli.py:197-203`)
   has no correction and no minimum OOS trade count.
4. **Sample size:** Live equity trades span only ~9 trading days (2026-04-29 → 05-12; bot was
   capacity-dead 05-25 → 06-11). high_watermark has **zero** live trades; gap_and_go has one.
   Strategy enable/disable decisions were made on this sample.
5. **Survivorship/selection:** Backfill scenarios come from the fixed `SYMBOLS` watchlist
   (`backfill/cli.py:43`) — hand-picked liquid names, single 252-day (mostly one-regime) window.
6. **Attribution validity:** S2 same-session matching is honest but excludes overnight carries;
   option strategies (12 bear_* variants) have no replay coverage at all, and live data shows
   pathological dispatch failure (e.g. bear_orb: 1,828 failed sells vs 384 filled).

What replay already does **right** (verified, no fix needed): entries fill on the *next* bar
(`runner.py:127-145`), stop checked before profit target when both touch in one bar
(`runner.py:77-85`, conservative), strategy sees only bars `[0..index]` (no entry look-ahead).

## Goal

Produce an evidence-backed verdict per strategy (**keep / disable / insufficient-data**), computed
with realistic costs and statistical significance — plus a documented list of methodology
weaknesses with evidence-backed recommendations. Paper trading; anything testable is fair game.

## Non-goals (YAGNI)

- No change to the nightly OOS gate logic in this pass (recommendations only — changing the
  production gate before measuring its failure mode is the same sin we're auditing).
- No intrabar tick simulation, partial fills, or market-impact modeling.
- No replay engine for option strategies (live-data-only analysis for those).
- No new dashboard surface.

## Design

Three parts: two small, fully-tested code components, then the analysis that uses them.

### Part 1 — Slippage cost model in ReplayRunner

New frozen `Settings` field `replay_slippage_bps: float` (env `REPLAY_SLIPPAGE_BPS`, default
**5.0**, validated ≥ 0). Applied **adversely on every simulated fill**, long-only semantics:

- Entry buy fill: `price × (1 + bps/10⁴)`
- Stop / EOD / profit-target sell fill: `price × (1 − bps/10⁴)`

Applied inside the four fill sites in `replay/runner.py` (`_process_existing_order`,
`_process_stop_hit`, `_process_profit_target_hit`, `_handle_eod_exit`). The PT fill-at-touch
optimism is deliberately absorbed into this knob rather than modeled separately. Equity updates
and event `exit_price` details use the slipped price, so `build_backtest_report` and all
downstream metrics (sweep scoring, OOS gate, session-eval comparisons) inherit costs with no
further changes. **Consequence (intended):** nightly sweep and OOS gate become cost-aware
the moment this deploys, because they run the same runner with `Settings.from_env()`.
Default 5 bps/side ≈ 10 bps round trip — conservative for liquid large-caps, lenient for
small-caps; the audit CLI runs a 0-vs-5 sensitivity explicitly so the choice is visible.

### Part 2 — Bootstrap statistics (`replay/stats.py`)

Pure module, no I/O, seeded `random.Random` for determinism:

- `bootstrap_mean_ci(values, *, n_resamples=2000, confidence=0.95, seed=42) -> (lo, hi)` —
  percentile bootstrap CI of the mean.
- `bootstrap_p_positive(values, *, n_resamples=2000, seed=42) -> float` — fraction of bootstrap
  means ≤ 0 (one-sided p-value for "mean per-trade P&L > 0").
- Returns `None` markers / degenerate handling for n < 5 (flagged `insufficient-data` rather
  than a misleading interval).

Inputs are per-trade P&L lists already computable from `ReplayResult` events / `BacktestReport`.

### Part 3 — `alpaca-bot-backtest audit` subcommand

Extends the existing backtest CLI (follows `run`/`compare`/`sweep` pattern):

```
alpaca-bot-backtest audit --scenario-dir data/backfill \
    [--strategies all] [--slippage-bps 5] [--limit N] [--output report.md] [--json results.json]
```

For each strategy in `STRATEGY_REGISTRY` × each scenario file: run replay **twice** (0 bps and
`--slippage-bps`), pool per-trade P&L across scenarios, and emit per strategy:

| metric | source |
|---|---|
| trades, win rate, profit factor, total P&L, annualized Sharpe | existing `BacktestReport` aggregation |
| bootstrap 95% CI on mean trade P&L, p(edge>0) | Part 2 |
| cost sensitivity: ΔP&L and Δwin-rate between 0 bps and N bps | the paired runs |
| verdict | `negative-edge` (CI hi < 0) / `no-evidence` (CI spans 0 or p ≥ 0.05) / `positive-edge` (CI lo > 0 with costs) / `insufficient-data` (n < 5) |

Markdown table to stdout/`--output`; machine-readable JSON via `--json`. Sequential execution,
`--limit` for smoke runs. No Postgres dependency — live comparison stays in existing
`alpaca-bot-weekly-review` / SQL.

### Part 4 — The contrarian evaluation report (analysis deliverable)

`docs/strategy-audit/2026-06-11-contrarian-strategy-evaluation.md`, written after running the
audit CLI over the 999 fresh scenarios in `/var/lib/alpaca-bot/nightly/scenarios`. Contents:

1. **Methodology audit** — the six weaknesses above, each with code reference and observed impact.
2. **Per-strategy verdict table (11 equity strategies)** — backtest-with-costs verdict, live P&L
   (S2-fixed `list_trade_pnl_by_strategy`), live n, agreement/disagreement between live and replay,
   and whether the current enabled/disabled flag is supported by evidence.
3. **Option strategies (live-only)** — premium collected vs buy-to-close per strategy, dispatch
   failure rates (e.g. bear_orb 1,828 failed), verdict on whether they should trade at all.
4. **Sweep integrity check** — tonight's R7 cost-blind sweep results vs cost-aware audit results:
   does the OOS gate pass strategies the audit calls `no-evidence`? Quantifies the
   multiple-comparisons concern with the actual top-10/gate numbers.
5. **Recommendations** (future work, not implemented here): OOS gate minimum trade count,
   gate on bootstrap CI rather than point Sharpe, watchlist rotation to reduce selection bias,
   multi-regime scenario windows, overnight-carry attribution.

### Operational constraints

- The R7 evolve container is running; the audit replay run (999 × 11 × 2 ≈ 22k single-combo
  replays) starts only **after** the sweep container exits, to avoid CPU contention and so the
  report can include sweep results. Smoke-test with `--limit 20` first.
- The slippage default changes nightly sweep scoring from its next run — called out in the
  report and commit message. `REPLAY_SLIPPAGE_BPS=0` restores old behavior.
- Live trading safety: replay/stats/CLI code paths never touch the broker; `evaluate_cycle()`
  purity unchanged; no new orders, no dispatch-path changes.

### Testing

TDD with project DI conventions (fake callables, in-memory data, no mocks):

- Runner slippage: synthetic scenario where one entry+stop, one entry+target, one EOD exit each
  produce exactly-computable slipped P&L; 0 bps reproduces current behavior (regression guard).
- Stats: deterministic seeded values — known CI on a fixed list, degenerate n<5, all-equal values.
- Audit CLI: tmp scenario dir with 2 tiny scenario JSONs, assert table + JSON structure and
  verdict classification boundaries.

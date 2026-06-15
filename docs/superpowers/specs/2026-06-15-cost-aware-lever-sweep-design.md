# Cost-aware lever sweep — design (2026-06-15)

> Sub-project 1 of the "get to profitable trading" program. Brainstormed via
> `/plan-and-refine` Stage 1. Source evidence:
> `docs/strategy-audit/2026-06-12-honest-reevaluation.md` (commit a8d6b73) and
> `/var/lib/alpaca-bot/nightly/audit-2026-06-12.json`.

## 1. Problem and evidence

Over the fixed replay harness (post commit `fe809c0`) at 5 bps/side across 999
scenarios, **zero of 11 strategies are positive-edge**. Four are negative-edge
with 95% confidence (breakout, momentum, ema_pullback, bb_squeeze). The failure
mode is **cost drag, not bad entries**: nine of eleven strategies are profitable
*frictionless* but churn more than their per-trade edge can pay for.

Lead candidate **bull_flag**: 66.4% win rate, +$2.15/trade after cost, 3179
trades, 95% CI mean/trade `[-0.8044, 5.1284]`, p_positive 0.088 — the closest
thing to significant. Secondary **vwap_reversion**: +$4.50/trade after cost but
only 549 trades, CI `[-7.79, 17.60]`, p 0.24. Both are frictionless-profitable
and dragged toward zero by cost; both have a positive after-cost point estimate
whose CI still straddles zero.

## 2. Thesis and objective function

**Thesis:** convert frictionless edge into *after-cost* edge by reducing churn
or raising per-trade edge — via selectivity, longer holds, and stronger entry
gates — so the per-trade edge clears the round-trip cost and the bootstrap CI
lower bound crosses zero.

**Objective function — `ci_low`.** The audit's verdict is
`positive-edge ⟺ ci_low > 0 AND p_positive < 0.05`. The single scalar that the
verdict turns on is `ci_low`, the bootstrap 95% CI lower bound on pooled mean
per-trade P&L. The harness ranks every grid point by **after-cost `ci_low`
descending**, with `mean_trade_pnl`, `trades`, `p_positive`, and `verdict`
reported alongside. We reuse the *existing, trusted* `run_audit`
(`src/alpaca_bot/replay/audit.py`) as the objective — we do **not** introduce a
second objective function. (The existing `tuning/sweep.py` optimizes a
Sharpe-first composite per-scenario — the wrong objective for this question and
explicitly out of scope here.)

### 2.1 The selectivity-vs-power tension (design-critical)

Cutting trade count raises the mean of what survives but **widens the CI**
(fewer bootstrap samples → more dispersion). Because the verdict rewards *both*
a high mean *and* enough samples to be significant, a lever that raises the mean
while decimating count can *lower* `ci_low`. The harness must therefore report
`trades` next to `ci_low` for every point, and the analyst must read them
together. A point with a great mean and 40 trades is not a candidate; a point
with a modest mean and 2,000 trades that lifts `ci_low > 0` is.

## 3. Non-levers (explicitly excluded)

- **Position sizing is not a lever.** Uniform scaling of every trade scales the
  mean *and* both CI bounds by the same factor, so the sign of `ci_low` — the
  verdict condition — is scale-invariant. Sizing fields
  (`risk_per_trade_pct`, `max_position_pct`, `max_notional_pct`, etc.) are
  excluded from the grid.
- **`ENTRY_TIMEFRAME_MINUTES` stays 15.** `Settings.validate()` hard-codes it;
  the strategy is coupled to 15-minute bars.
- **Hold time is bounded by one session.** `FLATTEN_TIME=15:45` flattens every
  open position at EOD, so "let winners run" is capped at one session day, not
  multi-day. Widening trailing stops or raising a profit target can only let a
  winner run *within* the session. The grid respects this bound — it does not
  attempt multi-day holds.

## 4. Architecture

A **cost-aware lever-sweep diagnostic** that reuses the audit objective and the
chronological IS/OOS splitter. No new production trading code path; no change to
`evaluate_cycle`, the supervisor, or the nightly cron.

### 4.1 New module: `src/alpaca_bot/replay/lever_sweep.py`

Pure-ish orchestration, no I/O of its own (scenarios passed in, rows returned):

```
LeverPoint        — frozen dataclass: label: str, overrides: dict[str, object]
                    (overrides are Settings *dataclass field* names → typed values)

LeverSweepRow     — frozen dataclass: label, overrides, is_row: StrategyAuditRow,
                    oos_row: StrategyAuditRow | None

run_lever_sweep(
    *,
    scenarios: list[ReplayScenario],     # full 999 (or a subset for a coarse pass)
    base_settings: Settings,             # the live-baseline reference Settings
    strategy: str,                       # "bull_flag" | "vwap_reversion"
    grid: list[LeverPoint],              # OFAT points (Section 5)
    slippage_bps: float = 5.0,
    walk_forward: bool = True,           # split each scenario IS/OOS and audit both
    in_sample_ratio: float = 0.8,
    daily_warmup: int = 30,
    pooled_trades_fn=...,                # injectable for tests (DI seam)
    on_progress=None,
) -> list[LeverSweepRow]
```

Mechanics:

1. If `walk_forward`, split every scenario once up front via
   `split_scenario(s, in_sample_ratio=, daily_warmup=)`
   (`src/alpaca_bot/replay/splitter.py`) into `(is_scenarios, oos_scenarios)`.
   Splitting is independent of the lever overrides, so it is done **once**, not
   per grid point.
2. For each `LeverPoint`, build `settings = dataclasses.replace(base_settings,
   **point.overrides)` and call
   `run_audit(scenarios=is_scenarios, settings=settings, strategies=[strategy],
   slippage_bps=slippage_bps, pooled_trades_fn=pooled_trades_fn)` → take the
   single `StrategyAuditRow` as `is_row`.
3. Rank all points by `is_row.ci_low` descending (NaN/None `ci_low` →
   `insufficient-data` → sorts last).
4. For the **top-K** points (default K=5) *and* the baseline, run `run_audit`
   again on `oos_scenarios` → `oos_row`. (OOS is only run for the shortlist to
   bound compute; the rest carry `oos_row=None`.)
5. Return the ranked `LeverSweepRow` list.

`dataclasses.replace` is the same mechanism `run_audit` itself uses
(`replace(settings, replay_slippage_bps=...)`), so override application is
already proven safe. Every override field in Section 5 is an independent,
already-validated Settings field within its documented range; the harness never
touches sizing or `entry_timeframe_minutes`.

### 4.2 New CLI subcommand: `alpaca-bot-backtest lever-sweep`

Added to `src/alpaca_bot/replay/cli.py` alongside `audit`. Responsibilities:
load scenarios from `--scenario-dir` (glob `*.json`, same as `_cmd_audit`,
honoring `--limit`), build `base_settings` from env (`Settings.from_env()`),
construct the OFAT grid for `--strategy`, call `run_lever_sweep`, and write a
Markdown findings report to `--output`.

```
alpaca-bot-backtest lever-sweep \
  --scenario-dir /data/scenarios \
  --strategy bull_flag \
  --slippage-bps 5 \
  [--limit N] [--coarse] [--no-walk-forward] \
  --output docs/strategy-audit/2026-06-15-lever-sweep-bull_flag.md
```

`--coarse` swaps in a reduced grid (best-guess values only) and is intended to
pair with `--limit` for a fast first pass; the default is the full OFAT grid on
all scenarios with walk-forward on.

### 4.3 Two-stage execution (compute control)

Each full audit run iterates all scenarios twice (costed + frictionless). With
~23 OFAT points per strategy this is heavy, and the host concurrently runs
nightly containers. The recommended run protocol:

1. **Coarse pass** — `--coarse --limit 200` to narrow each lever family to its
   best one or two values on a scenario subset (directional signal only).
2. **Full confirm** — full grid (or the narrowed grid) on all 999 scenarios with
   walk-forward, producing the decision-grade report.
3. **Combination stage** — take the single most-improving value from each of the
   top-2 lever families and run their small cross-product (≤9 points) on the
   full set with walk-forward, to catch interactions the one-factor-at-a-time
   sweep misses.

## 5. The lever grid (OFAT around the live baseline)

The reference point is the **live env config** the 2026-06-12 audit ran under
(confirmed by targeted env grep; no secrets read):

| field | live baseline |
|---|---|
| `atr_stop_multiplier` | 1.0 |
| `trailing_stop_atr_multiplier` | 1.5 (trailing ON) |
| `trailing_stop_profit_trigger_r` | 1.0 |
| `enable_profit_target` | False (no fixed target) |
| `profit_target_r` | 2.0 (inactive) |
| `enable_profit_trail` | True (`profit_trail_pct` 0.95) |
| `relative_volume_threshold` | 1.5 |
| `enable_regime_filter` | False |
| `enable_vwap_entry_filter` | True |
| `entry_window_start` / `entry_window_end` | 10:00 / 15:30 |
| `flatten_time` | 15:45 |

The grid is **one-factor-at-a-time (OFAT)**: each family varies a single field,
holding all others at baseline. Sum (not product) of family sizes keeps the run
tractable and isolates each lever's marginal effect on `ci_low`. The baseline
point is included once.

| family | Settings field(s) | values (baseline **bold**) | hypothesis |
|---|---|---|---|
| A — initial stop width | `atr_stop_multiplier` | 0.75, **1.0**, 1.5, 2.0 | wider stop → fewer premature stop-outs, fewer round trips |
| B — trailing aggressiveness | `trailing_stop_atr_multiplier` | 0.0 (off), 1.0, **1.5**, 2.5, 3.5 | looser trail → winners run further within the session |
| C — trailing trigger | `trailing_stop_profit_trigger_r` | 0.5, **1.0**, 1.5, 2.0 | later trail engagement → don't choke winners early |
| D — fixed profit target | `enable_profit_target` + `profit_target_r` | **off**, on@1.5, on@2.0, on@3.0, on@4.0 | a target can *raise* per-trade edge or cap it — test both directions |
| E — relative-volume selectivity | `relative_volume_threshold` | **1.5**, 2.0, 2.5, 3.0 | higher bar → fewer, higher-conviction entries |
| F — regime filter | `enable_regime_filter` | **off**, on | trade only with the daily regime → cut low-edge entries |
| G — VWAP entry filter | `enable_vwap_entry_filter` | off, **on** | confirm the live filter earns its selectivity |
| H — session restriction | `entry_window_end` | 12:00, 14:00, **15:30** | restrict to the higher-edge morning window |

~23 unique points per strategy after de-duplicating the shared baseline.
Families E–H are universal *entry* gates in `evaluate_cycle`; A–D are universal
*exit* mechanics in the replay runner — all apply regardless of which strategy's
signal evaluator fires, so the same grid is valid for both bull_flag and
vwap_reversion. bull_flag is the primary target (3179 trades → real power);
vwap_reversion is secondary (549 trades → lower power, watch the count column).

## 6. Built-in IS/OOS walk-forward (robustness, not promotion)

The project has been burned by in-sample sweeps. To avoid presenting an overfit
candidate, the harness runs its **own** chronological walk-forward using the
*same* audit objective: select grid points on the in-sample 80% (rank by IS
`ci_low`), then re-audit the top-K on the out-of-sample 20%. A candidate is only
interesting if its IS edge **survives OOS** — i.e. OOS `verdict` is not
negative-edge and OOS `ci_low` is at least non-negative (ideally `> 0`, subject
to the smaller OOS sample's lower power: ~20% of trades, so ~640 for bull_flag,
~110 for vwap_reversion).

**This OOS check is a diagnostic, not a promotion.** It is the harness telling
*itself* not to trust an in-sample mirage. It is computed with the audit
objective on a held-out slice — a *stronger*, profitability-aligned check than
the nightly gate's Sharpe composite. It does **not** change any config and does
**not** authorize live trading.

## 7. Gating — candidates only, promotion via the nightly OOS gate

The output of this sub-project is **candidate parameter sets, nothing more.**

- No config file is modified. `TRADING_MODE=paper` and
  `ENABLE_LIVE_TRADING=false` in `/etc/alpaca_bot/alpaca-bot.env` are not
  touched. The bot stays in `close_only`; this sub-project does **not** resume
  it.
- A grid point that reaches positive-edge in-sample (and survives the built-in
  OOS check) is still only a *candidate*. Promotion happens **only** through
  `alpaca-bot-nightly` → `candidate.env`, which is inert until an operator runs
  `scripts/apply_candidate.sh`. That is a separate, operator-gated sub-project
  (sub-project B).
- **Dependency flagged for sub-project B:** the current nightly `STRATEGY_GRIDS`
  (`tuning/sweep.py`) grids entry-shape params and ranks by Sharpe — it does
  **not** include the cost-drag/selectivity levers swept here
  (`profit_target_r`, `trailing_stop_*`, `atr_stop_multiplier`,
  `enable_regime_filter`, session windows). Promoting a lever-sweep candidate
  through nightly will require extending the nightly grid and/or its objective to
  cover these fields. Designing that is sub-project B's job, gated on this
  sub-project finding a candidate worth validating. It is noted here only as the
  hand-off boundary, not designed.

## 8. Output — findings report

`docs/strategy-audit/2026-06-15-lever-sweep-<strategy>.md`, containing:

1. **Baseline row** — IS and OOS audit of the live config (the reference point).
2. **Per-family OFAT table** — for each family, every value's IS
   `ci_low / mean / trades / p_positive / verdict` and `Δci_low` vs baseline.
3. **Family ranking** — families ordered by max `Δci_low`, naming the lever(s)
   that most improve after-cost edge (the literal answer to the sub-project's
   question).
4. **Top-K shortlist** — the K points with highest IS `ci_low`, each with its
   OOS row side-by-side, flagging which survive OOS.
5. **Combination-stage table** — the top-2 families' cross-product, IS + OOS.
6. **Candidate hand-off** — for any point reaching (or approaching) positive-edge
   that survives OOS: the exact Settings field/value overrides, written as the
   precise input sub-project B must route through the nightly OOS gate. If no
   point crosses, the report says so plainly — a null result is a valid,
   publishable outcome and must not be dressed up.

## 9. Testing (TDD, DI, no mocks)

Unit tests in `tests/unit/test_lever_sweep.py` using small synthetic scenarios
and the project's fake-callables pattern. The `pooled_trades_fn` parameter on
`run_audit` is the DI seam — tests inject a fake that records the `Settings` it
was called with and returns canned per-trade P&L lists, so we assert behavior
without running a full replay:

1. **Override propagation** — a `LeverPoint` with
   `overrides={"enable_profit_target": True, "profit_target_r": 3.0}` causes
   `run_audit` to receive a `Settings` with those exact field values (assert via
   the recording fake). Confirms `dataclasses.replace` wiring.
2. **Ranking by `ci_low`** — given a fake returning P&L lists that yield known
   `ci_low` ordering across three points, `run_lever_sweep` returns them sorted
   `ci_low` descending; `insufficient-data` (None `ci_low`) sorts last.
3. **Walk-forward split** — with `walk_forward=True` and a multi-day scenario,
   each shortlisted row has a non-None `oos_row`, and the IS/OOS scenario sets
   handed to the fake are disjoint in their trading dates (via `split_scenario`).
4. **Top-K OOS bound** — with K=2 and 5 grid points, exactly the 2 highest-IS
   points (plus baseline) get an `oos_row`; the rest carry `oos_row=None`.
5. **Determinism** — two identical runs return identical `ci_low` values
   (bootstrap seed=42 is inherited from `replay/stats.py`).
6. **CLI smoke** — `_cmd_lever_sweep` over a 2-file fixture dir writes a report
   containing the baseline row and the family ranking section.

Run `pytest` (full suite) before every commit; baseline is the current passing
count.

## 10. Constraints honored

- Reuses the trusted `run_audit`; no second objective function introduced.
- Read-only with respect to production config and trading status; no resume from
  `close_only`; `TRADING_MODE`/`ENABLE_LIVE_TRADING` untouched.
- Candidates only; promotion strictly via the nightly OOS gate (sub-project B).
- Runs entirely on the fixed harness (post `fe809c0`); no look-ahead — the
  splitter prepends a 30-bar daily warmup so OOS lookbacks have history without
  leaking future bars, and the runner's per-day `< day` daily slice is unchanged.
- `evaluate_cycle` stays pure; no new I/O inside the engine.
- Commit trailer on every commit:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## 11. Hand-off

On completion this sub-project yields either (a) one or more candidate Settings
override sets that reach positive after-cost edge in-sample and survive the
built-in OOS check — handed to sub-project B for promotion through the nightly
OOS gate — or (b) a documented null result identifying which levers move
`ci_low` and by how much, narrowing the search for the next iteration. Either
outcome is a real result; neither authorizes a live-config change here.

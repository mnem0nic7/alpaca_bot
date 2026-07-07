# Bull Flag Capacity Reduction - 2026-07-07

## Context

The July 6 paper proof window exposed clustered same-cycle exposure. `DDOG` and
`PANW` were both accepted in the same late-day candidate cluster, both filled,
and both exited via normal EOD flatten losses. Stop placement and cancellation
were operationally correct, but the cluster increased clean-window loss
concentration.

Initial profit-protection diagnostics did not justify a new exit lever:

- Breakeven/profit-stop updates were correctly rejected when the candidate stop
  would have been above the latest close.
- The existing off-default giveback/early-exit research did not survive the
  earlier per-trade CI/OOS validation.
- Longer entry-order lifetimes improved some in-sample fill counts but did not
  prove durable OOS edge.

The remaining direct lever is portfolio capacity. This audit tests whether the
paper proof should keep accepting up to four simultaneous positions or narrow to
the single strongest cross-sectional `bull_flag` candidate.

## Screen

Command:

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 160 \
  --sample-seed bull-flag-current-capacity-validation-20260707 \
  --slippage-bps 2 \
  --max-open-positions 1 \
  --max-open-positions 2 \
  --max-open-positions 3 \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --output /tmp/bull_flag_capacity_validation_160.md \
  --jsonl /tmp/bull_flag_capacity_validation_160.jsonl
```

Result:

| K | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 160 | 114 | 70.2% | 1.60 | 175.56 | 1.5400 | 3.18 | [0.0946, 3.0808] | 0.0175 | positive-edge |
| 2 | 160 | 145 | 67.6% | 1.23 | 94.96 | 0.6549 | 1.54 | [-0.7089, 2.0475] | 0.1170 | no-evidence |
| 3 | 160 | 153 | 66.0% | 1.13 | 69.13 | 0.4518 | 1.06 | [-0.9213, 1.8253] | 0.2025 | no-evidence |
| 4 | 160 | 157 | 65.6% | 1.16 | 80.12 | 0.5103 | 1.22 | [-0.8437, 1.8645] | 0.1670 | no-evidence |

## Independent Validation

Command:

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 240 \
  --sample-seed bull-flag-current-capacity-independent-20260707 \
  --slippage-bps 2 \
  --max-open-positions 1 \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --output /tmp/bull_flag_capacity_independent_240.md \
  --jsonl /tmp/bull_flag_capacity_independent_240.jsonl
```

Result:

| K | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 240 | 152 | 69.1% | 1.81 | 239.33 | 1.5745 | 3.48 | [0.4375, 2.7929] | 0.0035 | positive-edge |
| 4 | 240 | 217 | 65.0% | 1.42 | 214.02 | 0.9863 | 2.48 | [0.0058, 1.9734] | 0.0235 | positive-edge |

## Decision

Promote paper proof posture to `MAX_OPEN_POSITIONS=1`.

Rationale:

- K=1 was positive-edge in both independent samples.
- K=1 had the strongest profit factor, mean/trade, Sharpe, and CI floor in both
  samples.
- K=4 failed the 160-scenario screen and only narrowly cleared the independent
  validation lower bound.
- K=1 directly reduces the clustered same-cycle exposure that produced the July
  6 clean-window EOD loss cluster.

Tradeoff: K=1 will slow proof trade accumulation. That is acceptable because the
current objective is paper-mode profitability and risk control, not maximum
turnover.

This supersedes the previous paper capacity decisions in
`2026-06-26-bull-flag-portfolio-k-audit.md` and the July 6 second-strategy
triage notes that referenced `MAX_OPEN_POSITIONS=4`.

## Proof Epoch Reset

Reset the live paper proof start to `2026-07-07` after promoting
`MAX_OPEN_POSITIONS=1`. The July 6 paper losses were produced under the old
K=4 posture, so the go-forward profitability proof should measure the deployed
K=1 posture instead of forcing the new setup to earn back losses from the
superseded capacity regime.

## K=1 Proof-Horizon Follow-Up

The direct tradeoff from K=1 is slower proof collection. The proof-horizon
diagnostic now includes the live active-day requirement so the estimate matches
the current paper gate: 30 scoreable closed trades, at least $0.01 cumulative
P&L, and 5 active trade days.

Command:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
python3 -m alpaca_bot.replay.cli proof-horizon \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 240 \
  --sample-seed bull-flag-k1-proof-horizon-20260707 \
  --slippage-bps 2 \
  --max-open-positions 1 \
  --starting-equity 68991.62 \
  --min-trades 30 \
  --min-pnl 0.01 \
  --min-active-days 5 \
  --output /tmp/bull_flag_k1_proof_horizon_active_days_240.md \
  --json /tmp/bull_flag_k1_proof_horizon_active_days_240.json
```

Result:

| metric | value |
|---|---:|
| scenarios | 240 |
| sessions | 274 |
| trades | 125 |
| total P&L | $184.68 |
| active trade days | 108 |
| historical starts checked | 274 |
| starts that eventually reached proof gate | 204 |
| starts not proven by data end | 70 |
| eventual pass rate | 74.45% |
| starts reaching trade threshold | 233 |
| starts reaching active-day threshold | 268 |
| first-threshold pass rate | 74.68% |
| first-threshold failures that later recovered | 30 |
| median sessions to proof pass | 66 |
| p90 sessions to proof pass | 82 |
| p95 sessions to proof pass | 84 |
| slowest observed pass | 97 |

Conclusion: the active-day gate is not the binding historical constraint once
the 30-trade sample is reached. The binding cost of K=1 is elapsed market time:
the proof can be profitable, but the historical median start needed roughly
three months of sessions to pass and the 95th percentile needed roughly four.
Do not loosen the live proof gate just to accelerate the dashboard; the
operational expectation should be a slow but cleaner paper proof.

## K=1 Robust Proof-Horizon Follow-Up

The active-day horizon was still too optimistic because it modeled only trades,
P&L, and active days. The live proof also requires profit factor, profit
concentration, and EOD-loss-share robustness. The proof-horizon diagnostic now
accepts those thresholds and reports blocker counts at the first 30-trade
threshold and at the end of available data.

Command:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
python3 -m alpaca_bot.replay.cli proof-horizon \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 240 \
  --sample-seed bull-flag-k1-proof-horizon-20260707 \
  --slippage-bps 2 \
  --max-open-positions 1 \
  --starting-equity 68991.62 \
  --min-trades 30 \
  --min-pnl 0.01 \
  --min-active-days 5 \
  --min-profit-factor 1.20 \
  --max-single-win-pnl-share 0.50 \
  --max-eod-loss-share 0.50 \
  --output /tmp/bull_flag_k1_proof_horizon_robust_same_seed_240.md \
  --json /tmp/bull_flag_k1_proof_horizon_robust_same_seed_240.json
```

Same-seed result:

| metric | value |
|---|---:|
| scenarios | 240 |
| sessions | 274 |
| trades | 125 |
| total P&L | $184.68 |
| historical starts checked | 274 |
| starts that eventually reached proof gate | 0 |
| starts not proven by data end | 274 |
| starts reaching trade threshold | 233 |
| starts reaching active-day threshold | 268 |
| first-threshold pass rate | 0.00% |
| first-threshold blocker counts | eod_loss_share:233, positive_pnl:59, profit_concentration:119, profit_factor:89 |
| terminal blocker counts | active_days:6, eod_loss_share:251, positive_pnl:144, profit_concentration:67, profit_factor:150, sample_trades:41 |

Conclusion: K=1 fixes clustered exposure but does not, by itself, solve the
full robustness proof. The dominant historical failure is EOD-loss share:
every start that reached the 30-trade threshold had EOD-loss-share as a
blocker. The next profitable-paper weak point is exit quality, not proof
velocity. Do not scale capacity or enable a second strategy to hide this; find
an exit improvement that survives OOS, or let the live clean window prove that
recent K=1 behavior differs from this historical sample.

## K=1 Timing Follow-Up

The July 6 EOD-loss cluster left one plausible follow-up: with paper now capped
at one open position, maybe an earlier entry cutoff or earlier flatten would
reduce EOD loss share without sacrificing the K=1 edge. The timing levers were
rerun under the new live posture.

Command:

```bash
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-k1-timing-prefilter-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 68991.62 \
  --lever-label H_session:end=12:00 \
  --lever-label H_session:end=14:00 \
  --lever-label P_flatten:flatten=15:30,entry_end=15:15 \
  --lever-label P_flatten:flatten=15:15,entry_end=15:00 \
  --lever-label P_flatten:flatten=15:00,entry_end=14:45 \
  --lever-label P_flatten:flatten=14:45,entry_end=14:30 \
  --top-k 7 \
  --output /tmp/bull_flag_k1_timing_prefilter_80.md
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `P_flatten:flatten=15:15,entry_end=15:00` | -3.3871 | -0.8646 | 39 | `no-evidence` | -2.5508 | `no-evidence` |
| 2 | `baseline` | -3.3872 | -0.8403 | 39 | `no-evidence` | -3.4123 | `no-evidence` |
| 3 | `P_flatten:flatten=14:45,entry_end=14:30` | -3.5287 | -1.0955 | 37 | `no-evidence` | -3.4505 | `no-evidence` |
| 4 | `H_session:end=14:00` | -3.6255 | -0.9545 | 37 | `no-evidence` | -3.4123 | `no-evidence` |
| 5 | `P_flatten:flatten=15:00,entry_end=14:45` | -3.6473 | -1.0865 | 37 | `no-evidence` | -3.3266 | `no-evidence` |
| 6 | `P_flatten:flatten=15:30,entry_end=15:15` | -3.6537 | -1.1795 | 39 | `no-evidence` | -3.3352 | `no-evidence` |
| 7 | `H_session:end=12:00` | -4.2174 | -1.0754 | 33 | `no-evidence` | -4.3456 | `no-evidence` |

Conclusion: do not change `ENTRY_WINDOW_END` or `FLATTEN_TIME`. The best timing
row was statistically indistinguishable from baseline in-sample and still had a
negative OOS lower bound.

## K=1 Entry-Quality Lower-Bound Follow-Up

The July 7 readiness dry run found one accepted K=1 candidate and three
`close_too_far_below_entry` rejects. The standard lever grid already covered
the upper close-to-entry guard but did not cover the live lower bound
(`ENTRY_MIN_CLOSE_TO_ENTRY_PCT=-0.01`), so the replay grid now includes a
`W_min_close_to_entry` family.

Command:

```bash
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-k1-entry-min-close-prefilter-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 68991.62 \
  --lever-label W_min_close_to_entry:entry_min_close_to_entry_pct=-1.0 \
  --lever-label W_min_close_to_entry:entry_min_close_to_entry_pct=-0.05 \
  --lever-label W_min_close_to_entry:entry_min_close_to_entry_pct=-0.02 \
  --lever-label W_min_close_to_entry:entry_min_close_to_entry_pct=-0.005 \
  --lever-label W_min_close_to_entry:entry_min_close_to_entry_pct=0.0 \
  --top-k 6 \
  --output /tmp/bull_flag_k1_entry_min_close_prefilter_80.md
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `W_min_close_to_entry:entry_min_close_to_entry_pct=-0.02` | -1.4522 | 1.0623 | 48 | `no-evidence` | -3.8927 | `no-evidence` |
| 2 | `W_min_close_to_entry:entry_min_close_to_entry_pct=-1.0` | -1.4855 | 0.9891 | 46 | `no-evidence` | -6.1778 | `no-evidence` |
| 3 | `W_min_close_to_entry:entry_min_close_to_entry_pct=-0.05` | -1.4855 | 0.9891 | 46 | `no-evidence` | -6.1778 | `no-evidence` |
| 4 | `baseline` | -2.1547 | 0.0457 | 49 | `no-evidence` | -5.9516 | `no-evidence` |
| 5 | `W_min_close_to_entry:entry_min_close_to_entry_pct=-0.005` | -2.2290 | 0.2522 | 45 | `no-evidence` | -5.6448 | `no-evidence` |
| 6 | `W_min_close_to_entry:entry_min_close_to_entry_pct=0.0` | -5.0346 | -1.4544 | 19 | `no-evidence` | n/a | `insufficient-data` |

Conclusion: do not change `ENTRY_MIN_CLOSE_TO_ENTRY_PCT`. Loosening the lower
bound did not survive OOS, and tightening it damaged proof velocity without
producing a positive-edge row.

## K=1 Entry-Order Lifetime Follow-Up

The current proof needs more scoreable trades, so the `ENTRY_ORDER_ACTIVE_BARS`
diagnostic was retested under the live K=1 posture. A previous K=4 screen
showed more fills but no OOS edge; this pass checks whether the result changes
after capacity reduction.

Command:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-k1-entry-order-active-bars-prefilter-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 68991.62 \
  --lever-label AG_entry_order_active_bars:2 \
  --lever-label AG_entry_order_active_bars:3 \
  --top-k 3 \
  --output /tmp/bull_flag_k1_entry_order_active_bars_prefilter_80.md
```

Result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `AG_entry_order_active_bars:3` | 1.1376 | 2.5017 | 58 | `positive-edge` | -2.3454 | `no-evidence` |
| 2 | `AG_entry_order_active_bars:2` | 1.0058 | 2.6286 | 49 | `positive-edge` | -2.9063 | `no-evidence` |
| 3 | `baseline` | -0.0554 | 1.7238 | 38 | `no-evidence` | -2.5217 | `no-evidence` |

Because both longer-lived entry orders were tempting in-sample, the same
diagnostic received a larger independent 160-scenario validation:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 160 \
  --sample-seed bull-flag-k1-entry-order-active-bars-validation-20260707 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 68991.62 \
  --lever-label AG_entry_order_active_bars:2 \
  --lever-label AG_entry_order_active_bars:3 \
  --top-k 3 \
  --output /tmp/bull_flag_k1_entry_order_active_bars_validation_160.md
```

Validation result:

| rank | lever | IS ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---|---:|---|
| 1 | `AG_entry_order_active_bars:3` | 0.0829 | 1.3651 | 123 | `positive-edge` | -0.9701 | `no-evidence` |
| 2 | `AG_entry_order_active_bars:2` | -0.5840 | 0.9527 | 113 | `no-evidence` | -1.6702 | `no-evidence` |
| 3 | `baseline` | -0.7882 | 0.7970 | 95 | `no-evidence` | -0.9040 | `no-evidence` |

Conclusion: do not change `ENTRY_ORDER_ACTIVE_BARS`. The longer-lived orders
improved proof velocity on both samples, but no value held a non-negative OOS
lower bound. On the larger validation, `3` bars was slightly worse than
baseline OOS and `2` bars was materially worse. Keep the live value at `1`.

## Exit-Quality Follow-Up

The robust proof-horizon result above changed the exit-lever scoring question.
When judged against the same proof gate rather than only per-trade CI/OOS,
`V_giveback_exit:on@0.0025,max_return=0` was the only tested exit lever that
both improved robust starts-passed and turned the independent 240-scenario
sample positive. Paper proof was therefore promoted to:

- `ENABLE_GIVEBACK_EXIT=true`
- `GIVEBACK_EXIT_MIN_FAVORABLE_PCT=0.0025`
- `GIVEBACK_EXIT_MAX_RETURN_PCT=0.0`

No-follow-through and early-loss exits remain disabled. See
`docs/strategy-audit/2026-07-06-bull-flag-eod-loss-mitigation.md` for the
proof-horizon sweep tables.

## Post-Supervisor Execution Slice

The whole-day current-session execution diagnostic still includes earlier
2026-07-07 activity from before the latest deployed runtime posture and can
therefore keep reporting stale fill-rate, capacity, and short-window warnings.
`paper_proof_status.sh` now prints a separate
`paper proof post-supervisor execution` line, bounded by the latest
`supervisor_started` audit event, and `run_check_with_audit.sh` persists those
fields on scheduled proof checks.

Live verification after the 2026-07-07 19:08:57 UTC supervisor restart:

| slice | status | warnings | evaluated | signals | accepted | capacity rejected | entry orders | short windows |
|---|---|---|---:|---:|---:|---:|---:|---:|
| whole current session | `needs_work` | `settled_entry_fill_rate,capacity_rejections,short_entry_windows` | 24,188 | 2,038 | 14 | 1,961 | 12 | 1 |
| post-supervisor | `ok` | `none` | 2,760 | 5 | 0 | 0 | 0 | 0 |

Conclusion: keep the whole-day warning for forensics, but use the
post-supervisor slice to verify whether the currently deployed runtime repeats
the execution-quality problem. At this check it did not.

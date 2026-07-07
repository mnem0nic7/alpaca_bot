# Second strategy triage - 2026-07-06

Purpose: investigate the `strategy_diversification` proof blocker without
promoting an unproven strategy into paper trading.

Live context at triage time:

- Mode: `paper`
- Strategy version: `v1-breakout`
- Enabled strategy: `bull_flag`
- Proof blocker: `active=1 required=2`
- Current proof diagnostic: `candidate_status=no_approved_stock_strategy`

## Candidate-universe diagnostic hardening

The proof status now splits disabled candidates into stock-auditable and
option-gated groups. With `ENABLE_OPTIONS_TRADING=false`, the current paper
surface has one approved stock strategy active (`bull_flag`), ten disabled
stock candidates, and twelve disabled option/bear candidates that are gated
out of the stock-only proof universe.

The scale diagnostic also distinguishes approved active strategies from
approved replay-supported active strategies. Current replay scenarios and the
portfolio replay runner only carry stock bars; option chains, option prices,
and option fills are not part of the replay proof path. Until that exists,
option/bear factory names are reported with `option_replay_status=unsupported`
and cannot satisfy the second-strategy scale requirement, even if one is
manually allowlisted later.

This prevents the diversification blocker from looking easier than it is:
there is no approved disabled stock strategy waiting to be enabled, and the
option/bear factory names should not be counted as immediately promotable
second-strategy candidates while options are off or replay-unsupported.

During the frozen paper proof, `--allow-unapproved` is also blocked for stock
strategies that are not in `PAPER_APPROVED_STRATEGIES`. Intentional paper
experiments remain possible outside `PAPER_PROOF_FREEZE`, but the active proof
window cannot be contaminated by manually forcing on a strategy before replay
approval.

Command:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper
timeout 180s alpaca-bot-backtest audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategies orb,high_watermark,vwap_reversion,gap_and_go,vwap_cross,failed_breakdown \
  --sample-size 12 \
  --sample-seed second-strategy-triage-20260706 \
  --slippage-bps 2 \
  --output -
```

Completed rows before timeout:

| strategy | sample trades | verdict |
|---|---:|---|
| `orb` | 274 | `no-evidence` |
| `high_watermark` | 2 | `insufficient-data` |
| `vwap_reversion` | 2 | `insufficient-data` |

The run timed out after 180 seconds before producing verdicts for
`gap_and_go`, `vwap_cross`, and `failed_breakdown`.

## Follow-up bounded single-strategy reruns

Same environment, scenario sample, seed, and 2 bps/side cost assumption. Each
candidate was run separately with `timeout 120s`.

| strategy | completed output | sample trades | verdict |
|---|---|---:|---|
| `gap_and_go` | costed replay only before timeout | 0 | no candidate |
| `vwap_cross` | costed and frictionless replay completed in longer rerun | 55 | `no-evidence` |
| `failed_breakdown` | costed and frictionless replay completed | 17 | `no-evidence` |

`vwap_cross` details from the longer bounded rerun:

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `vwap_cross` | 12 | 55 | 56.4% | 0.86 | -16.62 | -0.3022 | -0.86 | [-1.9097, 1.0663] | 0.6630 | -5.89 | 10.73 | `no-evidence` |

`failed_breakdown` details:

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `failed_breakdown` | 12 | 17 | 70.6% | 2.14 | 21.39 | 1.2583 | 5.14 | [-0.5089, 3.0447] | 0.0870 | 25.10 | 3.71 | `no-evidence` |

## Larger failed-breakdown evidence check

Because `failed_breakdown` was the only sparse candidate with positive
after-cost P&L, it received a larger deterministic sample before any promotion
decision.

Command:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper
timeout 600s alpaca-bot-backtest audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategies failed_breakdown \
  --sample-size 120 \
  --sample-seed second-strategy-failed-breakdown-20260706 \
  --slippage-bps 2 \
  --output /tmp/failed_breakdown_audit_120.md \
  --json /tmp/failed_breakdown_audit_120.json
```

Result:

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `failed_breakdown` | 120 | 135 | 60.7% | 1.03 | 8.83 | 0.0654 | 0.20 | [-0.8767, 1.0071] | 0.4535 | 27.33 | 18.50 | `no-evidence` |

Conclusion: the larger sample collapses the apparent edge. The strategy remains
unapproved and should not be enabled as the second paper strategy.

## Full-history failed-breakdown 2 bps check

The 120-scenario sample left `failed_breakdown` as the only near-miss worth a
full-history check under the current paper cost assumption. It was rerun across
all available nightly scenarios at 2 bps/side.

Command:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper
timeout 1800s alpaca-bot-backtest audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategies failed_breakdown \
  --slippage-bps 2 \
  --output /tmp/failed_breakdown_audit_full_2bps.md \
  --json /tmp/failed_breakdown_audit_full_2bps.json \
  --jsonl /tmp/failed_breakdown_audit_full_2bps.jsonl
```

Result:

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `failed_breakdown` | 999 | 1,151 | 59.6% | 1.16 | 367.03 | 0.3189 | 1.39 | [-0.0575, 0.6997] | 0.0470 | 544.54 | 177.51 | `no-evidence` |

Conclusion: lower paper costs make `failed_breakdown` a near miss, not an
approved strategy. Its CI still crosses zero, so enabling it would turn the
diversification gate into curve-fitting pressure.

## Broader 2 bps sample and audit tooling hardening

A broader 60-scenario pass at the current paper cost assumption was attempted
for the remaining non-active equity candidates:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper
timeout 1200s alpaca-bot-backtest audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategies orb,high_watermark,ema_pullback,vwap_reversion,gap_and_go,vwap_cross,bb_squeeze,failed_breakdown \
  --sample-size 60 \
  --sample-seed second-strategy-broad-20260706 \
  --slippage-bps 2 \
  --output /tmp/second_strategy_broad_60.md \
  --json /tmp/second_strategy_broad_60.json
```

The run timed out before final output, which exposed a tooling weakness: audit
rows were only written after the entire strategy list completed. Console
progress before timeout showed no promotable strategy:

| strategy | scenarios | trades | verdict | evidence durability |
|---|---:|---:|---|---|
| `orb` | 60 | 1,486 | `no-evidence` | terminal progress only |
| `high_watermark` | 60 | 0 | `insufficient-data` | terminal progress only |
| `ema_pullback` | 60 | 284 | `no-evidence` | terminal progress only |
| `vwap_reversion` | 60 | 10 | `no-evidence` | terminal progress only |

Tooling fix: `alpaca-bot-backtest audit` now supports `--jsonl FILE`, which
checkpoints one flushed JSON row per completed strategy. That makes long
candidate searches robust to timeouts.

Follow-up checkpointed reruns:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper; \
  alpaca-bot-backtest audit \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategies gap_and_go,vwap_cross,bb_squeeze,failed_breakdown \
    --sample-size 60 \
    --sample-seed second-strategy-broad-20260706 \
    --slippage-bps 2 \
    --output /tmp/second_strategy_remaining_60.md \
    --json /tmp/second_strategy_remaining_60.json \
    --jsonl /tmp/second_strategy_remaining_60.jsonl'
```

The grouped rerun timed out after checkpointing `gap_and_go`; `vwap_cross` and
`bb_squeeze` were then rerun separately with the same sample and cost
assumptions.

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `gap_and_go` | 60 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | `insufficient-data` |
| `vwap_cross` | 60 | 304 | 60.9% | 1.02 | 15.24 | 0.0501 | 0.19 | [-0.5937, 0.6933] | 0.4395 | 84.40 | 69.16 | `no-evidence` |
| `bb_squeeze` | 60 | 204 | 51.5% | 0.84 | -70.93 | -0.3477 | -1.23 | [-1.1402, 0.3759] | 0.8335 | -23.65 | 47.28 | `no-evidence` |

Conclusion: none of the broader 2 bps checks produced a second strategy worth
paper promotion. The checkpointing improvement should be kept; the strategy
flags should not change.

## Resume-enabled all-equity 80-scenario scan

The earlier broad scan did not explicitly recheck `breakout` or `momentum`,
and grouped runs were still tedious after timeouts. The audit CLI now supports
`--resume-jsonl`, which reads a prior checkpoint, validates matching scenario
count and slippage, preserves completed rows, and skips only the already
checkpointed strategies.

Command, resumed across timeout boundaries:

```bash
timeout 1200s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli audit \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategies breakout,momentum,orb,high_watermark,ema_pullback,vwap_reversion,gap_and_go,vwap_cross,bb_squeeze,failed_breakdown \
    --sample-size 80 \
    --sample-seed second-strategy-all-equity-20260706 \
    --slippage-bps 2 \
    --output /tmp/second_strategy_all_equity_80.md \
    --json /tmp/second_strategy_all_equity_80.json \
    --jsonl /tmp/second_strategy_all_equity_80.jsonl'
```

After `gap_and_go` again showed zero costed trades and stalled in the
frictionless pass, the final resume omitted only that already-disqualified
strategy to complete the remaining candidates:

```bash
timeout 1200s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli audit \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategies breakout,momentum,orb,high_watermark,ema_pullback,vwap_reversion,vwap_cross,bb_squeeze,failed_breakdown \
    --sample-size 80 \
    --sample-seed second-strategy-all-equity-20260706 \
    --slippage-bps 2 \
    --output /tmp/second_strategy_all_equity_80_no_gap.md \
    --json /tmp/second_strategy_all_equity_80_no_gap.json \
    --jsonl /tmp/second_strategy_all_equity_80.jsonl \
    --resume-jsonl'
```

Result:

| strategy | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| `breakout` | 80 | 253 | 0.94 | -35.94 | -0.1420 | [-0.8910, 0.5466] | 9.94 | 45.88 | `no-evidence` |
| `momentum` | 80 | 898 | 1.00 | -9.52 | -0.0106 | [-0.4473, 0.4423] | 142.68 | 152.20 | `no-evidence` |
| `orb` | 80 | 1,860 | 1.07 | 251.81 | 0.1354 | [-0.1383, 0.4034] | 578.88 | 327.07 | `no-evidence` |
| `high_watermark` | 80 | 3 | 4.60 | 16.01 | 5.3373 | n/a | 16.77 | 0.76 | `insufficient-data` |
| `ema_pullback` | 80 | 353 | 0.98 | -15.25 | -0.0432 | [-0.6119, 0.5915] | 45.71 | 60.96 | `no-evidence` |
| `vwap_reversion` | 80 | 40 | 1.63 | 100.56 | 2.5139 | [-1.7801, 7.5042] | 105.38 | 4.82 | `no-evidence` |
| `gap_and_go` | 80 | 0 | n/a | 0.00 | n/a | n/a | n/a | n/a | `insufficient-data` |
| `vwap_cross` | 80 | 362 | 0.98 | -14.82 | -0.0409 | [-0.7778, 0.8046] | 60.96 | 75.78 | `no-evidence` |
| `bb_squeeze` | 80 | 283 | 0.95 | -26.56 | -0.0938 | [-0.7540, 0.5797] | 46.08 | 72.64 | `no-evidence` |
| `failed_breakdown` | 80 | 84 | 1.56 | 93.61 | 1.1144 | [-0.6591, 3.1445] | 108.09 | 14.48 | `no-evidence` |

`breakout`, `momentum`, and `orb` all had enough trades to be meaningful, but
their after-cost confidence intervals still crossed zero. `vwap_reversion` and
`failed_breakdown` were profitable on this sample, but both remained too
uncertain to promote. `gap_and_go` again produced no costed trades.

Conclusion: the full registered equity set still has no approved second
strategy under the current paper cost assumption. The diversification blocker
is real; do not solve it by enabling a weak strategy.

## Failed-breakdown lever sweep null

Because the full-history 2 bps failed-breakdown audit was close enough to tempt
a manual promotion, it received a focused coarse lever sweep using the actual
failed-breakdown controls. The sweep was run on a deterministic 60-scenario
sample at 2 bps/side, with walk-forward enabled.

Command:

```bash
timeout 900s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli lever-sweep \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategy failed_breakdown \
    --sample-size 60 \
    --sample-seed failed-breakdown-lever-20260706 \
    --slippage-bps 2 \
    --coarse \
    --top-k 5 \
    --output /tmp/failed_breakdown_lever_coarse_60.md'
```

Result:

| rank | lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---|---:|---|
| 1 | `H_session:end=14:00` | -0.9185 | 38 | `no-evidence` | -7.5097 | `negative-edge` |
| 2 | `A_initial_stop:atr_stop_multiplier=1.5` | -0.9332 | 54 | `no-evidence` | -6.3980 | `negative-edge` |
| 3 | `baseline` | -0.9346 | 54 | `no-evidence` | -6.0582 | `negative-edge` |
| 4 | `B_trail_atr:trailing_stop_atr_multiplier=2.5` | -0.9346 | 54 | `no-evidence` | -6.0582 | `negative-edge` |
| 5 | `C_trail_trigger:trailing_stop_profit_trigger_r=1.5` | -0.9346 | 54 | `no-evidence` | -6.0582 | `negative-edge` |
| 8 | `J_failed_breakdown_recapture:failed_breakdown_recapture_buffer_pct=0.002` | -1.0390 | 40 | `no-evidence` | n/a | n/a |
| 9 | `I_failed_breakdown_volume:failed_breakdown_volume_ratio=2.5` | -3.1552 | 27 | `no-evidence` | n/a | n/a |

The failed-breakdown-specific selectivity levers did not improve the edge.
Raising the volume threshold made the sample thinner and materially worse, and
tightening the recapture buffer still left a negative CI lower bound.

Tooling fix: `alpaca-bot-backtest lever-sweep` now passes the strategy name
into the grid builder. For `failed_breakdown`, the generic
`relative_volume_threshold` lever is omitted because the strategy ignores it,
and `failed_breakdown_volume_ratio` plus
`failed_breakdown_recapture_buffer_pct` are swept instead.

Conclusion: the failed-breakdown near miss does not survive nearby parameter or
walk-forward checks. It remains unapproved.

Decision:

- No second strategy is approved from this triage.
- No live or paper strategy flags were changed.
- The proof blocker should remain `strategy_diversification`. `orb` and
  `vwap_cross`, `bb_squeeze`, `ema_pullback`, and `failed_breakdown` completed
  with `no-evidence`; the full-history `failed_breakdown` 2 bps check was
  close but still below the positive-edge threshold. `high_watermark`,
  `vwap_reversion`, and `gap_and_go` were too sparse on these samples.

Follow-up:

- Do not spend more paper capital on these candidates without a new thesis or
  stronger offline evidence.
- If a new candidate survives a first pass, promote it only after a separate OOS
  handoff note documents sample size, cost assumptions, and failure modes.

## Zero-trade audit skip and momentum recheck

The broad scans exposed one more tooling drag: when a strategy produced zero
costed trades, the audit still launched the frictionless replay. That cannot
produce a meaningful cost-drag estimate for a zero-trade candidate and had
previously made `gap_and_go` grouped scans waste timeout budget.

Tooling fix: `run_audit()` now skips the frictionless replay when the costed
trade list is empty, emitting a zero cost-drag `insufficient-data` row
immediately. This preserves JSONL checkpointing and makes broad candidate scans
less fragile.

A fresh checkpointed 80-scenario all-equity screen with the skip in place found
one sample-specific positive row:

| strategy | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `breakout` | 80 | 222 | 0.80 | -110.89 | -0.4995 | [-1.4429, 0.4874] | 0.8405 | -67.99 | 42.90 | `no-evidence` |
| `momentum` | 80 | 872 | 1.34 | 586.58 | 0.6727 | [0.2160, 1.1554] | 0.0030 | 728.70 | 142.12 | `positive-edge` |

Because prior momentum samples were weaker, the positive row was treated as a
candidate only and rerun on a larger deterministic 240-scenario sample:

| strategy | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `momentum` | 240 | 2,490 | 1.12 | 583.42 | 0.2343 | [-0.0185, 0.4893] | 0.0380 | 1,007.05 | 423.63 | `no-evidence` |

Conclusion: `momentum` is a near miss on the larger validation, not an approved
second strategy. The diversification blocker remains real. Keep the zero-trade
audit skip because it speeds future candidate scans without changing strategy
behavior or approval criteria.

## Replay hotpath fixes for multi-session intraday scans

The grouped all-equity scan still became slow inside `orb`, even after JSONL
checkpointing preserved prior rows. Root cause: both `orb` and `gap_and_go`
rebuilt current-session context by slicing and filtering the entire historical
intraday prefix on every bar. In replay, that prefix spans multiple sessions,
so the scan can degrade as scenarios get longer.

Tooling fix:

- `orb` now walks backward only through the signal bar's current session before
  computing the opening range.
- `gap_and_go` now checks whether the signal bar is the first observed bar of
  its session by comparing only the immediately previous bar's session.
- `vwap_cross` now walks backward only through the signal bar's current session
  before computing prior/current VWAP context.
- Regression tests guard against reintroducing whole-prefix slices.

Smoke command:

```bash
timeout 180s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli audit \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategies orb,gap_and_go \
    --sample-size 12 \
    --sample-seed strategy-scan-hotpath-20260706 \
    --slippage-bps 2 \
    --output /tmp/orb_gap_hotpath_smoke.md'
```

Result:

| strategy | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `orb` | 12 | 327 | 1.10 | 57.47 | 0.1758 | [-0.3763, 0.7150] | 0.2515 | 112.27 | 54.79 | `no-evidence` |
| `gap_and_go` | 12 | 0 | n/a | 0.00 | n/a | n/a | n/a | 0.00 | 0.00 | `insufficient-data` |

`vwap_cross` was then rerun on the same 80-scenario seed that had timed out
during the grouped scan, and completed both replay phases:

| strategy | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `vwap_cross` | 80 | 409 | 0.92 | -71.92 | -0.1758 | [-0.8164, 0.4263] | 0.7080 | -2.17 | 69.75 | `no-evidence` |

The full resumed 80-scenario all-equity scan then completed:

| strategy | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---|
| `breakout` | 80 | 222 | 0.80 | -110.89 | -0.4995 | [-1.4429, 0.4874] | 0.8405 | `no-evidence` |
| `momentum` | 80 | 872 | 1.34 | 586.58 | 0.6727 | [0.2160, 1.1554] | 0.0030 | `positive-edge` |
| `orb` | 80 | 1,707 | 1.07 | 226.55 | 0.1327 | [-0.1272, 0.4015] | 0.1530 | `no-evidence` |
| `high_watermark` | 80 | 4 | n/a | 19.56 | 4.8907 | n/a | n/a | `insufficient-data` |
| `ema_pullback` | 80 | 384 | 0.97 | -19.03 | -0.0496 | [-0.5758, 0.4652] | 0.5630 | `no-evidence` |
| `vwap_reversion` | 80 | 25 | 0.73 | -44.83 | -1.7932 | [-8.0158, 5.0867] | 0.7070 | `no-evidence` |
| `gap_and_go` | 80 | 1 | n/a | 40.61 | 40.6080 | n/a | n/a | `insufficient-data` |
| `vwap_cross` | 80 | 409 | 0.92 | -71.92 | -0.1758 | [-0.8164, 0.4263] | 0.7080 | `no-evidence` |
| `bb_squeeze` | 80 | 231 | 1.20 | 76.79 | 0.3324 | [-0.3201, 0.9311] | 0.1550 | `no-evidence` |
| `failed_breakdown` | 80 | 109 | 0.92 | -19.00 | -0.1743 | [-1.2983, 0.8873] | 0.6425 | `no-evidence` |

Conclusion: no second strategy was approved. `momentum` remains the only
positive row on this seed, and it already failed the larger 240-scenario
validation above. The candidate-search path is now less fragile, which should
preserve operator time for future candidates that actually show evidence.

## Cross-sectional portfolio prefilter

Single-strategy replay can overstate trade availability because each strategy
gets its own isolated equity path. A pooled portfolio prefilter was added to
test candidate strategies under the paper account's current cross-sectional
constraint: one equity pool, K=4 open positions, $68,991.62 starting equity,
and 2 bps/side slippage. This is a read-only diagnostic and does not change
production strategy flags.

Command:

```bash
timeout 1200s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli portfolio-audit \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategy failed_breakdown \
    --strategy vwap_reversion \
    --strategy momentum \
    --strategy orb \
    --strategy breakout \
    --sample-size 80 \
    --sample-seed second-strategy-portfolio-prefilter-20260707 \
    --slippage-bps 2 \
    --max-open-positions 4 \
    --starting-equity 68991.62 \
    --output /tmp/second_strategy_portfolio_prefilter_80.md \
    --jsonl /tmp/second_strategy_portfolio_prefilter_80.jsonl'
```

Result:

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | 95% CI mean/trade | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| `failed_breakdown` | 80 | 82 | 62.2% | 1.02 | 3.50 | 0.0427 | [-1.9117, 2.2428] | 16.67 | 13.17 | `no-evidence` |
| `vwap_reversion` | 80 | 26 | 65.4% | 1.61 | 59.28 | 2.2800 | [-3.1262, 8.1869] | 62.74 | 3.46 | `no-evidence` |
| `momentum` | 80 | 516 | 61.8% | 1.21 | 185.18 | 0.3589 | [-0.1391, 0.8337] | 262.08 | 76.90 | `no-evidence` |
| `orb` | 80 | 828 | 58.3% | 1.06 | 89.16 | 0.1077 | [-0.2925, 0.5222] | 208.58 | 119.42 | `no-evidence` |
| `breakout` | 80 | 160 | 53.8% | 1.11 | 34.41 | 0.2151 | [-0.6901, 1.1159] | 70.32 | 35.91 | `no-evidence` |

`momentum` was the best near miss by sample size and CI lower bound, but the
after-cost confidence interval still crossed zero. It received a larger
portfolio validation before any promotion decision.

## Momentum portfolio validation

Command:

```bash
timeout 1200s bash -lc 'set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a; \
  export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src; \
  python3 -m alpaca_bot.replay.cli portfolio-audit \
    --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
    --strategy momentum \
    --sample-size 160 \
    --sample-seed second-strategy-portfolio-momentum-validation-20260707 \
    --slippage-bps 2 \
    --max-open-positions 4 \
    --starting-equity 68991.62 \
    --output /tmp/momentum_portfolio_validation_160.md \
    --jsonl /tmp/momentum_portfolio_validation_160.jsonl'
```

Result:

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `momentum` | 160 | 992 | 59.2% | 1.07 | 133.31 | 0.1344 | 0.60 | [-0.2945, 0.5440] | 0.2695 | 311.82 | 178.51 | `no-evidence` |

Conclusion: the portfolio-aware validation weakens the case for `momentum`.
It remains a useful research lead, not an approved second paper strategy. The
diversification blocker should remain until a stock strategy clears both
single-strategy evidence and pooled portfolio validation.

## Momentum focused lever check

The generic replay grid did not previously cover `momentum`'s
`PRIOR_DAY_HIGH_LOOKBACK_BARS` setting, so the grid now includes a
momentum-specific `U_prior_high_lookback` family. A focused 80-scenario lever
check tested that missing knob plus the strongest near-miss rows from a
timed-out coarse scan.

Command shape:

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper PYTHONPATH=src
python3 - <<'PY'
# custom LeverPoint grid:
# baseline, regime:on, session:end=14:00, stop_limit_buffer=0.00025,
# entry_stop_buffer=0.03, max_close_to_entry=0.005,
# flatten=15:15/entry_end=15:00, prior_high_lookback=2/3/5
PY
```

Result:

| rank | lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---|---:|---|
| 1 | `S_entry_stop_buffer:entry_stop_price_buffer=0.03` | -0.4840 | 611 | `no-evidence` | 0.1977 | `positive-edge` |
| 2 | `P_flatten:flatten=15:15,entry_end=15:00` | -0.5239 | 518 | `no-evidence` | -0.3537 | `no-evidence` |
| 3 | `F_regime:on` | -0.5274 | 497 | `no-evidence` | -0.6068 | `no-evidence` |
| 4 | `baseline` | -0.6004 | 654 | `no-evidence` | -0.1645 | `no-evidence` |
| 6 | `U_prior_high_lookback:prior_day_high_lookback_bars=2` | -0.6438 | 540 | `no-evidence` | -0.8096 | `no-evidence` |
| 7 | `U_prior_high_lookback:prior_day_high_lookback_bars=3` | -0.6500 | 483 | `no-evidence` | -1.0876 | `no-evidence` |
| 9 | `U_prior_high_lookback:prior_day_high_lookback_bars=5` | -0.9015 | 432 | `no-evidence` | -0.9136 | `no-evidence` |
| 10 | `T_max_close_to_entry:entry_max_close_to_entry_pct=0.005` | -1.5800 | 164 | `no-evidence` | 0.0869 | `positive-edge` |

`entry_stop_buffer=0.03` and `max_close_to_entry=0.005` had positive OOS rows,
but both failed in-sample. The prior-high lookback variants all failed both IS
and OOS while reducing trade count.

Conclusion: no momentum parameter change is approved, and momentum remains
unapproved as the second paper strategy. Keep the new grid coverage so future
momentum scans measure the real strategy-specific lookback parameter.

## Portfolio-targeted momentum lever check

The isolated lever sweep could not answer whether a near-miss lever still helps
after candidates compete for the same paper slots. Tooling now supports
portfolio-scored lever sweeps and exact lever-label filtering:

- `lever-sweep --portfolio` routes each grid point through the shared-equity
  top-K portfolio replay.
- `--max-open-positions` and `--starting-equity` let the sweep mirror the
  paper account sizing posture.
- repeated `--lever-label` arguments keep expensive portfolio sweeps targeted
  while automatically retaining `baseline` for deltas.

Command:

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy momentum \
  --sample-size 40 \
  --sample-seed second-strategy-portfolio-prefilter-20260707 \
  --slippage-bps 2 \
  --coarse \
  --portfolio \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --top-k 2 \
  --lever-label A_initial_stop:atr_stop_multiplier=1.5 \
  --lever-label S_entry_stop_buffer:entry_stop_price_buffer=0.03 \
  --lever-label T_max_close_to_entry:entry_max_close_to_entry_pct=0.005 \
  --lever-label U_prior_high_lookback:prior_day_high_lookback_bars=2 \
  --output /tmp/momentum_portfolio_lever_targeted_40.md
```

Result:

| rank | lever | IS ci_low | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---|---:|---|
| 1 | `S_entry_stop_buffer:entry_stop_price_buffer=0.03` | -0.1118 | 212 | `no-evidence` | -0.6564 | `no-evidence` |
| 2 | `baseline` | -0.1903 | 217 | `no-evidence` | -0.6567 | `no-evidence` |
| 3 | `A_initial_stop:atr_stop_multiplier=1.5` | -0.3223 | 215 | `no-evidence` | n/a | n/a |
| 4 | `T_max_close_to_entry:entry_max_close_to_entry_pct=0.005` | -0.3519 | 64 | `no-evidence` | n/a | n/a |
| 5 | `U_prior_high_lookback:prior_day_high_lookback_bars=2` | -0.6535 | 175 | `no-evidence` | n/a | n/a |

Conclusion: no portfolio-scored momentum lever is approved. The least-bad IS
row, `entry_stop_buffer=0.03`, failed OOS almost identically to baseline.
Momentum remains a research lead, not an enabled second strategy.

## Portfolio basket audit

The single-strategy portfolio audits still did not answer the exact paper
question: does adding a candidate to `bull_flag` improve the combined posture
after both strategies compete for the same top-K slots? Replay tooling now
includes `portfolio-basket-audit`, which evaluates a repeated `--strategy`
basket sequentially in one shared-equity portfolio, matching runtime's
strategy loop more closely than isolated candidate scans.

Implementation notes:

- Portfolio working orders now preserve the originating `strategy_name`, so a
  filled replay position can be evaluated and exited only by its own strategy.
- Basket replay blocks duplicate symbols across strategies and consumes global
  `max_open_positions` slots as earlier strategies emit entries.
- The command keeps the same sampling, starting-equity override, progress, and
  JSONL behavior as `portfolio-audit`.

Basket prefilter command shape:

```bash
python3 -m alpaca_bot.replay.cli portfolio-basket-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --strategy orb \
  --sample-size 40 \
  --sample-seed second-strategy-basket-prefilter-20260707 \
  --slippage-bps 2 \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --output /tmp/second_strategy_basket_prefilter_40.md
```

The same 40-scenario seed was used for the near-miss candidates:

| basket | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---|
| `bull_flag+momentum` | 40 | 315 | 1.06 | 39.22 | 0.1245 | [-0.5861, 0.8275] | 0.3695 | `no-evidence` |
| `bull_flag+orb` | 40 | 522 | 1.47 | 426.50 | 0.8170 | [0.2975, 1.3622] | 0.0025 | `positive-edge` |
| `bull_flag+failed_breakdown` | 40 | 74 | 0.81 | -29.49 | -0.3986 | [-1.7901, 0.8326] | 0.7195 | `no-evidence` |
| `bull_flag+vwap_reversion` | 40 | 45 | 1.23 | 24.32 | 0.5405 | [-1.6881, 2.8414] | 0.3195 | `no-evidence` |
| `bull_flag+breakout` | 40 | 130 | 0.93 | -20.08 | -0.1545 | [-1.1868, 0.8244] | 0.6135 | `no-evidence` |

`bull_flag+orb` was the only basket survivor, so it received a larger
independent validation:

```bash
python3 -m alpaca_bot.replay.cli portfolio-basket-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --strategy orb \
  --sample-size 160 \
  --sample-seed second-strategy-basket-orb-validation-20260707 \
  --slippage-bps 2 \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --output /tmp/second_strategy_basket_orb_validation_160.md
```

Validation result:

| basket | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `bull_flag+orb` | 160 | 1,333 | 1.10 | 263.36 | 0.1976 | [-0.1517, 0.5276] | 0.1385 | 509.06 | 245.70 | `no-evidence` |

Conclusion: no basket is approved for paper. `orb` is now the best
diversification research lead because it survived the 40-scenario basket
prefilter, but the larger validation rejected it. Keep `orb` disabled and keep
`PAPER_APPROVED_STRATEGIES=bull_flag`.

## Bull-flag plus ORB K-sensitivity check

Because `bull_flag+orb` was the only basket-level near miss, a follow-up tested
whether the shared position cap was the failure mode. The same 160-scenario
validation seed was rerun at K=2, K=3, and K=4 with all other assumptions held
constant.

Command:

```bash
python3 -m alpaca_bot.replay.cli portfolio-basket-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --strategy orb \
  --sample-size 160 \
  --sample-seed second-strategy-basket-orb-validation-20260707 \
  --slippage-bps 2 \
  --max-open-positions 2 \
  --max-open-positions 3 \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --output /tmp/second_strategy_basket_orb_k_sensitivity_160.md \
  --jsonl /tmp/second_strategy_basket_orb_k_sensitivity_160.jsonl
```

Result:

| K | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---:|---:|---:|---:|---:|---|---:|---:|---|
| 2 | 766 | 1.11 | 195.80 | 0.2556 | [-0.2526, 0.7409] | 0.1550 | 136.66 | `no-evidence` |
| 3 | 1,059 | 1.10 | 225.11 | 0.2126 | [-0.1931, 0.6223] | 0.1415 | 185.09 | `no-evidence` |
| 4 | 1,333 | 1.10 | 263.36 | 0.1976 | [-0.1517, 0.5276] | 0.1385 | 245.70 | `no-evidence` |

Conclusion: reducing shared capacity is not enough to approve `orb`. Smaller K
reduces trade count and cost drag, but every tested cap still has a negative CI
lower bound. Keep `MAX_OPEN_POSITIONS=4` and keep `orb` disabled.

## Bull-flag plus ORB runtime-priority checks

Basket replay initially used full sizing equity for every strategy. Runtime is
more conservative: each strategy's sizing equity is multiplied by its
confidence score, and a newly enabled strategy with no positive history starts
near the confidence floor. The basket audit command now supports repeatable
`--confidence-scale STRATEGY=SCALE` flags so this can be measured directly.

Confidence-scaled validation:

```bash
python3 -m alpaca_bot.replay.cli portfolio-basket-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --strategy orb \
  --sample-size 160 \
  --sample-seed second-strategy-basket-orb-validation-20260707 \
  --slippage-bps 2 \
  --max-open-positions 4 \
  --starting-equity 68991.62 \
  --confidence-scale orb=0.25 \
  --output /tmp/second_strategy_basket_orb_scaled_160.md \
  --jsonl /tmp/second_strategy_basket_orb_scaled_160.jsonl
```

Result:

| basket | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `bull_flag+orb` with `orb=0.25` | 1,333 | 1.10 | 272.29 | 0.2043 | [-0.1451, 0.5273] | 0.1265 | 237.01 | `no-evidence` |

Because runtime sorts equal-confidence strategies by registry order, and
`orb` is registered before `bull_flag`, an order-sensitivity pass also tested
`orb` first:

| basket | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `orb+bull_flag` | 1,300 | 1.10 | 255.49 | 0.1965 | [-0.1489, 0.5385] | 0.1230 | 232.97 | `no-evidence` |

Conclusion: neither confidence-floor sizing nor ORB-first priority rescues the
candidate. `orb` remains unapproved.

## Bull-flag plus ORB selectivity lever check

Because `orb` remained the best rejected basket lead, a follow-up tested simple
ORB/basket selectivity knobs before discarding it: opening-range width,
relative-volume threshold, and VWAP entry filtering. This was run as a
read-only basket replay; no paper flags were changed.

80-scenario screen:

| lever | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `baseline` | 799 | 1.23 | 325.60 | 0.4075 | [-0.0328, 0.8355] | 0.0370 | 143.14 | `no-evidence` |
| `ORB_opening_bars=1` | 943 | 1.10 | 191.17 | 0.2027 | [-0.2313, 0.6320] | 0.1800 | 202.49 | `no-evidence` |
| `ORB_opening_bars=3` | 661 | 1.23 | 238.31 | 0.3605 | [-0.0488, 0.7894] | 0.0470 | 132.68 | `no-evidence` |
| `ORB_opening_bars=4` | 579 | 1.06 | 60.65 | 0.1047 | [-0.3439, 0.5684] | 0.3255 | 99.19 | `no-evidence` |
| `relative_volume_threshold=2.5` | 671 | 1.24 | 274.29 | 0.4088 | [-0.0287, 0.8610] | 0.0385 | 120.35 | `no-evidence` |
| `relative_volume_threshold=3.0` | 549 | 1.33 | 293.36 | 0.5343 | [0.0157, 1.0412] | 0.0190 | 99.93 | `positive-edge` |
| `ENABLE_VWAP_ENTRY_FILTER=true` | 796 | 1.25 | 353.74 | 0.4444 | [0.0017, 0.8995] | 0.0250 | 142.17 | `positive-edge` |

The 80-scenario screen produced two apparent survivors. Because both settings
are global posture settings, not ORB-only settings, the larger validation scored
the whole `bull_flag+orb` basket under each candidate.

160-scenario independent validation:

| lever | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `baseline` | 1,316 | 1.15 | 387.55 | 0.2945 | [-0.0357, 0.6281] | 0.0405 | 233.76 | `no-evidence` |
| `relative_volume_threshold=3.0` | 1,088 | 1.05 | 115.44 | 0.1061 | [-0.2417, 0.4722] | 0.2945 | 172.15 | `no-evidence` |
| `ENABLE_VWAP_ENTRY_FILTER=true` | 1,325 | 1.14 | 357.05 | 0.2695 | [-0.0701, 0.6034] | 0.0675 | 260.50 | `no-evidence` |
| `relative_volume_threshold=3.0 + ENABLE_VWAP_ENTRY_FILTER=true` | 1,087 | 1.08 | 175.55 | 0.1615 | [-0.1847, 0.5308] | 0.1870 | 179.58 | `no-evidence` |

Conclusion: the simple ORB selectivity levers did not survive independent
validation. Keep `RELATIVE_VOLUME_THRESHOLD=2.0`,
`ENABLE_VWAP_ENTRY_FILTER=false`, and `ORB_OPENING_BARS=2` in paper.

## Remaining stock basket prefilter

After the first basket pass covered `momentum`, `orb`, `failed_breakdown`,
`vwap_reversion`, and `breakout`, the remaining disabled stock candidates were
scored as direct companions to the current paper posture. This used the same
portfolio basket frame: `bull_flag + candidate`, 40 scenarios, 2 bps/side
slippage, `MAX_OPEN_POSITIONS=4`, and `$68,991.62` starting equity.

Command output:

```text
/tmp/second_strategy_basket_remaining_prefilter_40.md
```

Result:

| basket | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `bull_flag+bb_squeeze` | 96 | 0.85 | -30.08 | -0.3134 | [-1.6103, 0.8062] | 0.7080 | 16.09 | `no-evidence` |
| `bull_flag+ema_pullback` | 181 | 1.15 | 52.59 | 0.2906 | [-0.6400, 1.3345] | 0.2940 | 32.71 | `no-evidence` |
| `bull_flag+gap_and_go` | 22 | 0.91 | -6.03 | -0.2741 | [-3.6712, 2.7432] | 0.5450 | 8.88 | `no-evidence` |
| `bull_flag+high_watermark` | 23 | 0.81 | -15.08 | -0.6555 | [-4.0659, 2.4456] | 0.6420 | 9.01 | `no-evidence` |
| `bull_flag+vwap_cross` | 190 | 1.20 | 89.15 | 0.4692 | [-0.7238, 1.7011] | 0.2085 | 36.74 | `no-evidence` |

Conclusion: none of the remaining disabled stock candidates earned promotion
to the 160-scenario validation step. `vwap_cross` had the best point estimate,
but the lower confidence bound stayed negative. Keep the paper allowlist at
`bull_flag` only.

## ORB market-context basket follow-up

After the replay bundle was enriched with VIX proxy and sector ETF daily bars,
the best rejected diversification lead, `bull_flag+orb`, was retested with
market-context gates. This was read-only: paper config was not changed.

80-scenario context prefilter:

| gate | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `ENABLE_SECTOR_FILTER=true` | 680 | 1.28 | 327.88 | 0.4822 | [0.0586, 0.9079] | 0.0135 | 125.26 | `positive-edge` |
| `ENABLE_VIX_FILTER=true` | 787 | 1.16 | 227.50 | 0.2891 | [-0.1256, 0.7209] | 0.0830 | 143.29 | `no-evidence` |
| `ENABLE_VIX_FILTER=true + ENABLE_SECTOR_FILTER=true` | 600 | 1.26 | 271.73 | 0.4529 | [-0.0065, 0.9509] | 0.0275 | 108.55 | `no-evidence` |

The sector-only gate was the lone survivor, so it received an independent
160-scenario validation:

| gate | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `ENABLE_SECTOR_FILTER=true` | 887 | 1.13 | 225.25 | 0.2539 | [-0.1436, 0.6586] | 0.1105 | 171.86 | `no-evidence` |

Conclusion: sector gating does not rescue `orb` as a second paper strategy.
Keep `orb` disabled and keep `ENABLE_SECTOR_FILTER=false`.

## K=1 live-posture basket screen

After the 2026-07-07 capacity audit promoted paper proof posture to
`MAX_OPEN_POSITIONS=1`, the full registered stock-strategy universe was
rescored as `bull_flag + candidate` baskets under the new live cap. Each
candidate used confidence-floor sizing (`candidate=0.25`), 2 bps/side slippage,
`$68,991.62` starting equity, and the deterministic
`second-strategy-k1-prefilter-20260707` 80-scenario sample.

Command shape:

```bash
python3 -m alpaca_bot.replay.cli portfolio-basket-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --strategy <candidate> \
  --sample-size 80 \
  --sample-seed second-strategy-k1-prefilter-20260707 \
  --slippage-bps 2 \
  --max-open-positions 1 \
  --starting-equity 68991.62 \
  --confidence-scale <candidate>=0.25 \
  --output /tmp/second_strategy_k1_<candidate>_prefilter_80.md \
  --jsonl /tmp/second_strategy_k1_<candidate>_prefilter_80.jsonl
```

Result:

| basket | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `bull_flag+bb_squeeze` | 192 | 0.72 | -109.67 | -0.5712 | [-1.2753, 0.1482] | 0.9380 | 36.60 | `no-evidence` |
| `bull_flag+breakout` | 152 | 1.04 | 10.30 | 0.0677 | [-0.8277, 1.0269] | 0.4405 | 36.41 | `no-evidence` |
| `bull_flag+ema_pullback` | 241 | 1.14 | 59.50 | 0.2469 | [-0.4413, 0.9304] | 0.2305 | 40.33 | `no-evidence` |
| `bull_flag+failed_breakdown` | 120 | 1.09 | 20.93 | 0.1744 | [-0.9031, 1.3804] | 0.4015 | 20.34 | `no-evidence` |
| `bull_flag+gap_and_go` | 56 | 1.22 | 28.85 | 0.5151 | [-1.5316, 2.8338] | 0.3280 | 8.76 | `no-evidence` |
| `bull_flag+high_watermark` | 58 | 1.21 | 27.65 | 0.4768 | [-1.5742, 2.6873] | 0.3270 | 9.20 | `no-evidence` |
| `bull_flag+momentum` | 302 | 0.86 | -103.12 | -0.3414 | [-1.1552, 0.4457] | 0.8000 | 58.37 | `no-evidence` |
| `bull_flag+orb` | 363 | 0.93 | -56.54 | -0.1558 | [-0.7885, 0.4678] | 0.7060 | 55.94 | `no-evidence` |
| `bull_flag+vwap_cross` | 244 | 0.95 | -28.32 | -0.1161 | [-0.9016, 0.6965] | 0.6210 | 41.76 | `no-evidence` |
| `bull_flag+vwap_reversion` | 79 | 0.78 | -68.24 | -0.8639 | [-3.0446, 1.3950] | 0.7780 | 11.56 | `no-evidence` |

Conclusion: the K=1 live posture does not uncover an approved second strategy.
Every registered stock companion still has a negative confidence lower bound.
Keep `PAPER_APPROVED_STRATEGIES=bull_flag`; the diversification blocker is real
and should not be bypassed by enabling an unproven strategy.

## Proof Gate Semantics

Because the K=1 screen found no statistically defensible second strategy, the
paper proof gate now separates profitability evidence from scale readiness.
`strategy_diversification` remains a scale blocker and is still shown in the
proof robustness detail, but it no longer blocks the one-strategy paper-profit
evidence verdict. This avoids making the profitability proof structurally
unreachable while preserving the stricter requirement before scaling beyond the
current paper posture. The scale requirement counts only approved,
replay-supported active strategies; option strategies remain ineligible for
that count until option replay is added and validated.

## Giveback-posture K=1 basket retest

After `V_giveback_exit:on@0.0025,max_return=0` was promoted for paper proof,
the full K=1 stock-companion basket screen was rerun with the same
`second-strategy-k1-prefilter-20260707` 80-scenario sample, 2 bps/side
slippage, `$68,991.62` starting equity, `MAX_OPEN_POSITIONS=1`, and
candidate confidence scaling at `0.25`.

Result:

| basket | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---|---:|---:|---|
| `bull_flag+ema_pullback` | 267 | 1.39 | 122.70 | 0.4596 | [-0.0553, 0.9914] | 0.0455 | 39.42 | `no-evidence` |
| `bull_flag+vwap_cross` | 271 | 1.16 | 58.93 | 0.2174 | [-0.3797, 0.8406] | 0.2435 | 43.63 | `no-evidence` |
| `bull_flag+breakout` | 160 | 1.28 | 59.22 | 0.3701 | [-0.3918, 1.2590] | 0.1705 | 38.18 | `no-evidence` |
| `bull_flag+orb` | 436 | 1.01 | 9.67 | 0.0222 | [-0.4525, 0.4539] | 0.4665 | 63.22 | `no-evidence` |
| `bull_flag+failed_breakdown` | 133 | 1.15 | 29.94 | 0.2251 | [-0.7327, 1.3343] | 0.3205 | 22.29 | `no-evidence` |
| `bull_flag+momentum` | 345 | 0.89 | -65.58 | -0.1901 | [-0.8220, 0.4804] | 0.7360 | 82.05 | `no-evidence` |
| `bull_flag+bb_squeeze` | 201 | 0.79 | -64.40 | -0.3204 | [-0.9221, 0.2526] | 0.8460 | 44.33 | `no-evidence` |
| `bull_flag+gap_and_go` | 59 | 1.49 | 46.73 | 0.7920 | [-0.9880, 2.8246] | 0.1990 | 17.38 | `no-evidence` |
| `bull_flag+high_watermark` | 61 | 1.47 | 45.53 | 0.7465 | [-1.0340, 2.6920] | 0.1995 | 17.83 | `no-evidence` |
| `bull_flag+vwap_reversion` | 84 | 0.89 | -21.79 | -0.2594 | [-1.9118, 1.4284] | 0.6130 | 19.87 | `no-evidence` |

`ema_pullback` was the only near miss, so it received a larger independent
160-scenario validation:

| basket | scenarios | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| `bull_flag+ema_pullback` | 160 | 370 | 1.28 | 142.47 | 0.3851 | [-0.1194, 0.9245] | 0.0760 | 60.62 | `no-evidence` |

Conclusion: the promoted giveback exit does not rescue any second stock
strategy. Keep `PAPER_APPROVED_STRATEGIES=bull_flag`; the diversification
blocker remains a real scale blocker, not a paper-proof profitability blocker.

## EMA-pullback period follow-up

The giveback-posture retest left `ema_pullback` as the closest rejected
companion. That exposed one coverage gap in the replay sweep grid:
`EMA_PERIOD`, the strategy's core moving-average knob, was not represented.
The lever grid now includes an `AH_ema_period` family for `ema_pullback`, and
the coarse grid includes `AH_ema_period:ema_period=7` for fast scans.

A direct costed basket screen then varied only `EMA_PERIOD` under the same live
paper posture: `bull_flag+ema_pullback`, `MAX_OPEN_POSITIONS=1`,
`ema_pullback=0.25` confidence sizing, 2 bps/side slippage, `$68,991.62`
starting equity, the promoted giveback exit, and the deterministic
`second-strategy-k1-prefilter-20260707` 80-scenario sample. Output JSON:
`/tmp/second_strategy_k1_giveback_ema_period_basket_costed_80.json`.

Result:

| EMA period | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | verdict |
|---:|---:|---:|---:|---:|---|---:|---|
| 9 | 267 | 1.39 | 122.70 | 0.4596 | [-0.0553, 0.9914] | 0.0455 | `no-evidence` |
| 20 | 221 | 1.31 | 77.48 | 0.3506 | [-0.1712, 0.8584] | 0.0940 | `no-evidence` |
| 7 | 278 | 1.28 | 98.12 | 0.3530 | [-0.1799, 0.8760] | 0.0990 | `no-evidence` |
| 12 | 249 | 1.22 | 62.77 | 0.2521 | [-0.2269, 0.7479] | 0.1585 | `no-evidence` |
| 5 | 303 | 1.06 | 23.13 | 0.0764 | [-0.4211, 0.5608] | 0.3695 | `no-evidence` |

Conclusion: the default period 9 remains the least-bad setting, but its
confidence interval still crosses zero and it already failed the independent
160-scenario validation above. No EMA-period variant is approved, and
`ema_pullback` remains disabled for paper proof.

## Vwap-cross relative-volume lookback approval

The next coverage pass expanded the lever grid beyond generic risk/exits to
include setup-specific knobs for registered stock candidates:
`breakout_lookback_bars`, `relative_volume_lookback_bars`,
`breakout_stop_buffer_pct`, `orb_opening_bars`,
`high_watermark_lookback_days`, `vwap_dip_threshold_pct`, `gap_threshold_pct`,
`gap_volume_threshold`, and the Bollinger squeeze controls. This closed a
search-surface weakness before another promotion decision.

A K=1 giveback-posture basket prefilter then tested the highest-value newly
covered knobs on the same deterministic
`second-strategy-k1-prefilter-20260707` 80-scenario sample. Output JSON:
`/tmp/second_strategy_k1_giveback_setup_knob_prefilter_80.json`.

Result:

| candidate | lever | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | verdict |
|---|---|---:|---:|---:|---:|---|---:|---|
| `vwap_cross` | `relative_volume_lookback_bars=10` | 285 | 1.59 | 193.74 | 0.6798 | [0.0824, 1.3183] | 0.0135 | `positive-edge` |
| `breakout` | `relative_volume_lookback_bars=10` | 158 | 1.32 | 71.83 | 0.4546 | [-0.3634, 1.3667] | 0.1440 | `no-evidence` |
| `vwap_cross` | `breakout_stop_buffer_pct=0.0005` | 271 | 1.16 | 58.93 | 0.2174 | [-0.3797, 0.8406] | 0.2435 | `no-evidence` |
| `breakout` | `breakout_stop_buffer_pct=0.0005` | 160 | 1.28 | 59.22 | 0.3701 | [-0.3918, 1.2590] | 0.1705 | `no-evidence` |
| `breakout` | `breakout_lookback_bars=10` | 193 | 1.13 | 37.84 | 0.1960 | [-0.5815, 0.9847] | 0.3050 | `no-evidence` |
| `high_watermark` | `high_watermark_lookback_days=126` | 72 | 1.38 | 40.88 | 0.5677 | [-0.8877, 2.1920] | 0.2405 | `no-evidence` |
| `gap_and_go` | `gap_threshold_pct=0.01` | 59 | 1.49 | 46.73 | 0.7920 | [-0.9880, 2.8246] | 0.1990 | `no-evidence` |
| `gap_and_go` | `gap_volume_threshold=1.5` | 59 | 1.49 | 46.73 | 0.7920 | [-0.9880, 2.8246] | 0.1990 | `no-evidence` |
| `bb_squeeze` | `bb_squeeze_threshold_pct=0.05` | 247 | 0.62 | -170.30 | -0.6895 | [-1.2720, -0.1343] | 0.9905 | `negative-edge` |
| `vwap_reversion` | `vwap_dip_threshold_pct=0.02` | 76 | 0.92 | -14.41 | -0.1896 | [-1.9144, 1.7119] | 0.5985 | `no-evidence` |

The single survivor was `bull_flag+vwap_cross` with
`RELATIVE_VOLUME_LOOKBACK_BARS=10`, so it received an independent 160-scenario
validation on seed `second-strategy-k1-giveback-vwap-cross-rvl-validation-20260707`.

Validation result:

| basket | scenarios | trades | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| `bull_flag+vwap_cross`, `relative_volume_lookback_bars=10` | 160 | 391 | 1.63 | 329.31 | 0.8422 | 2.74 | [0.2513, 1.4723] | 0.0040 | 342.46 | 13.14 | `positive-edge` |

Guard check: on the same validation seed, `bull_flag` alone remained
`no-evidence` under both the current and new relative-volume lookbacks, but the
new lookback improved point estimates while reducing cost drag:

| posture | trades | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | cost drag | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| `bull_flag`, `relative_volume_lookback_bars=20` | 122 | 1.52 | 102.06 | 0.8365 | 2.14 | [-0.2599, 2.2760] | 0.0810 | 14.12 | `no-evidence` |
| `bull_flag`, `relative_volume_lookback_bars=10` | 78 | 1.75 | 109.32 | 1.4015 | 2.83 | [-0.3765, 3.5135] | 0.0645 | 7.64 | `no-evidence` |

Conclusion: approve `vwap_cross` as the second replay-supported paper stock
strategy and promote `RELATIVE_VOLUME_LOOKBACK_BARS=10` for the paper proof
posture. This directly addresses the `strategy_diversification` scale blocker
without weakening the one-strategy evidence gate.

## Readiness dry-run hardening

After enabling `vwap_cross`, the paper readiness audit still dry-ran only the
primary `bull_flag` strategy. That left a controllable operational gap: the
enabled second strategy was replay-approved and live, but not exercised by the
same pre-open decision-path gate as the primary strategy.

The deploy and readiness scripts now build their decision dry-run strategy list
from `PAPER_READINESS_DECISION_DRY_RUN_STRATEGIES`, defaulting through
`PAPER_APPROVED_STRATEGIES`. The primary strategy still emits the canonical
`paper decision dry run ok:` line for existing audit consumers, while
additional approved strategies emit `paper readiness additional decision dry run
ok:` lines and fail readiness if they cannot meet the same coverage and
accepted-entry requirements.

The readiness audit now also persists a compact full-set summary,
`paper readiness decision dry run strategies ok: strategies=... count=...`.
`paper_readiness_if_needed`, lock-busy handling, and proof status validate that
summary before accepting a prior pass, so a stale one-strategy readiness audit
cannot satisfy the current two-strategy paper posture.

Forced readiness verification on 2026-07-07, using the 2026-07-06 completed
session and sample times `10:30,11:30,12:30,13:30,14:30,15:30`, passed for both
active strategies:

| strategy | decision records | accepted | entry intents | sample |
|---|---:|---:|---:|---|
| `bull_flag` | 948 | 1 | 1 | `NPCE:31.746@17.18` |
| `vwap_cross` | 948 | 1 | 1 | `HIMS:10.3626@38.66` |

Conclusion: the live two-strategy paper posture is now covered by both replay
approval and pre-open decision-path evidence.

## Activity audit hardening

The mid-session `paper_activity` check originally inspected only
`PAPER_ACTIVITY_STRATEGY`, which defaulted to `bull_flag`. After adding
`vwap_cross`, that left a post-open evidence gap: `bull_flag` activity could
pass while the second approved strategy had no fresh decision cycles, no
decision-log records, stale pending entries, or unmaterialized accepted
signals.

`paper_activity_check.sh` now builds `PAPER_ACTIVITY_STRATEGIES`, defaulting
through `PAPER_APPROVED_STRATEGIES`, and keeps the existing deep primary
diagnostic while adding an aggregate check for every approved activity
strategy. The aggregate gate fails the activity audit when any approved
strategy is missing decision evidence, missing required decision-log evidence,
has too few records without exposure, has blocked latest entries, has stale
pending entry orders, or has accepted decisions that did not materialize into
orders or positions.

The live paper env and compose config now carry
`PAPER_ACTIVITY_STRATEGIES=bull_flag,vwap_cross`, so the first due post-open
activity audit on 2026-07-07 will validate both active strategies.

## Post-close proof hardening

The post-close `session_guard`, cumulative `paper_profit_probe`, and proof
status scorer also had a primary-strategy assumption. They passed
`--strategy bull_flag` into `alpaca-bot-session-eval` and counted proof trades,
P&L, execution quality, and recent-pass audit reuse against the primary
strategy only.

`alpaca-bot-session-eval` now accepts `--strategies` for an explicit approved
basket. `session_guard.sh` and `paper_profit_probe.sh` build
`SESSION_GUARD_STRATEGIES` and `PROFIT_PROBE_STRATEGIES`, defaulting through
`PAPER_APPROVED_STRATEGIES`, and pass the normalized basket to the evaluator.
Their scheduled-check context and lock-busy reuse now persist and match the
full `strategies=` value, so a stale one-strategy post-close pass cannot
unlock the current two-strategy posture.

`paper_proof_status.sh` now scores progress over the approved basket as well:
closed trades, P&L, active days, unpaired exits, entry fill quality, and
capacity rejection quality all use the same `bull_flag,vwap_cross` filter.
Live verification on 2026-07-07 reports the basket in proof context and keeps
the only remaining evidence blockers at `sample_trades,active_days`.

The live env now states the same scale and execution-quality gates explicitly,
and the code default for `PROFIT_PROBE_START_DATE` was aligned to `2026-07-07`
so direct admin paths cannot fall back to the retired June proof window.

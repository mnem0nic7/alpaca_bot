# 2026-07-08 Second Strategy Scale Grid Validation

## Context

The paper-proof blocker remains `strategy_diversification`: only `bull_flag`
is approved and active, while the proof gate requires two approved replay-backed
strategies.

This scan used the read-only second-strategy basket scanner after adding:

- candidate scale grid: `0.10,0.25,0.50`
- independent validation of positive prefilter survivors
- bounded parallel scan jobs: `2`
- default exclusion: `vwap_cross`

No live strategy, paper approval allowlist, or trading parameter was changed.

## Artifacts

```text
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/summary.json
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/validation/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/validation/summary.json
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/validation_extra/orb_scale_0_10_validation.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/validation_extra/orb_scale_0_10_validation.jsonl
```

The scan updated:

```text
/var/lib/alpaca-bot/nightly/second_strategy/latest
/var/lib/alpaca-bot/nightly/second_strategy/latest_validation
```

## Prefilter Result

The 80-scenario prefilter found 19 positive-edge rows. Best per candidate:

| candidate | scale | trades | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `vwap_reversion` | 0.25 | 72 | 235.10 | [0.9670, 6.0543] | `positive-edge` |
| `ema_pullback` | 0.50 | 284 | 386.30 | [0.5703, 2.2382] | `positive-edge` |
| `gap_and_go` | 0.10 | 49 | 141.66 | [0.2999, 6.0981] | `positive-edge` |
| `high_watermark` | 0.10 | 49 | 141.66 | [0.2999, 6.0981] | `positive-edge` |
| `failed_breakdown` | 0.10 | 130 | 168.04 | [0.1551, 2.7017] | `positive-edge` |
| `bb_squeeze` | 0.10 | 175 | 162.43 | [0.1478, 1.9327] | `positive-edge` |
| `orb` | 0.10 | 452 | 269.17 | [0.0408, 1.1884] | `positive-edge` |

`breakout` and `momentum` did not produce positive-edge rows.

## Independent Validation

Validation used 160 scenarios, seed `second-strategy-independent-validation`,
2 bps/side slippage, K=1, and the same candidate scale selected by the prefilter.
The scheduled validation cap was still `SECOND_STRATEGY_MAX_VALIDATION_CANDIDATES=6`
for this run, so `orb` was validated immediately afterward with the same
independent validation settings.

| candidate | scale | trades | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `ema_pullback` | 0.50 | 351 | 168.60 | [-0.0807, 1.1487] | `no-evidence` |
| `failed_breakdown` | 0.10 | 191 | 112.77 | [-0.0909, 1.4134] | `no-evidence` |
| `bb_squeeze` | 0.10 | 286 | 13.28 | [-0.3402, 0.4296] | `no-evidence` |
| `orb` | 0.10 | 551 | 98.98 | [-0.2648, 0.6330] | `no-evidence` |
| `high_watermark` | 0.10 | 68 | -2.41 | [-1.1999, 1.2399] | `no-evidence` |
| `gap_and_go` | 0.10 | 68 | -6.22 | [-1.3481, 1.2568] | `no-evidence` |
| `vwap_reversion` | 0.25 | 89 | -23.54 | [-2.0141, 1.6840] | `no-evidence` |

Conclusion: no candidate from this batch is approved for paper promotion.
Keep `PAPER_APPROVED_STRATEGIES` at `bull_flag` until a candidate survives
independent validation.

Follow-up automation change: the scanner default was changed to
`SECOND_STRATEGY_MAX_VALIDATION_CANDIDATES=0`, which means validate every
positive prefilter survivor family unless an operator intentionally sets a cap.

## Uncapped Validation Refresh

After the proof status began reporting second-strategy evidence, the latest
scheduled artifact showed complete prefilter evidence but partial validation
coverage: `orb` was a positive prefilter survivor and was only covered by the
manual `validation_extra` run.

An uncapped refresh was run with `SECOND_STRATEGY_MAX_VALIDATION_CANDIDATES=0`
and `SECOND_STRATEGY_SCAN_JOBS=2`:

```text
/var/lib/alpaca-bot/nightly/second_strategy/20260708T030517Z/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T030517Z/summary.json
/var/lib/alpaca-bot/nightly/second_strategy/20260708T030517Z/validation/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T030517Z/validation/summary.json
```

The scan found the same 19 positive prefilter rows across 7 candidate families:
`vwap_reversion`, `ema_pullback`, `gap_and_go`, `high_watermark`,
`failed_breakdown`, `bb_squeeze`, and `orb`.

Independent validation used the 160-scenario
`second-strategy-independent-validation` sample and selected the best prefilter
scale for every survivor family:

| candidate | scale | trades | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `ema_pullback` | 0.50 | 351 | 168.60 | [-0.0807, 1.1487] | `no-evidence` |
| `failed_breakdown` | 0.10 | 191 | 112.77 | [-0.0909, 1.4134] | `no-evidence` |
| `orb` | 0.10 | 551 | 98.98 | [-0.2648, 0.6330] | `no-evidence` |
| `bb_squeeze` | 0.10 | 286 | 13.28 | [-0.3402, 0.4296] | `no-evidence` |
| `high_watermark` | 0.10 | 68 | -2.41 | [-1.1999, 1.2399] | `no-evidence` |
| `gap_and_go` | 0.10 | 68 | -6.22 | [-1.3481, 1.2568] | `no-evidence` |
| `vwap_reversion` | 0.25 | 89 | -23.54 | [-2.0141, 1.6840] | `no-evidence` |

The proof status now reports
`candidate_status=no_positive_validation_edge`,
`missing_validation_families=none`, `validation_rows=7`,
`validation_positive_rows=0`, and `promotion_approved=false`.

Conclusion: the strategy diversification blocker remains real. Do not promote
or enable a second strategy from this scan.

## Setup-Knob Search Automation

The default basket scanner only retests registered stock strategy families at
different confidence scales. A separate read-only wrapper now covers the
strategy-specific setup knobs that were previously checked through one-off
commands:

```bash
scripts/second_strategy_setup_knob_scan.sh /etc/alpaca_bot/alpaca-bot.env
```

The wrapper runs `bull_flag + candidate` basket audits with temporary env
overrides such as `EMA_PERIOD=7`, `ORB_OPENING_BARS=3`,
`HIGH_WATERMARK_LOOKBACK_DAYS=126`, `VWAP_DIP_THRESHOLD_PCT=0.02`,
`GAP_THRESHOLD_PCT=0.01`, `GAP_VOLUME_THRESHOLD=1.5`, the Bollinger squeeze
knobs, `FAILED_BREAKDOWN_VOLUME_RATIO=2.5`,
`FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT=0.002`, and
`PRIOR_DAY_HIGH_LOOKBACK_BARS=2`.

The wrapper refuses to vary the protected paper-proof execution parameters:
`ENTRY_ORDER_ACTIVE_BARS`, `ENTRY_MIN_CLOSE_TO_ENTRY_PCT`,
`STOP_LIMIT_BUFFER_PCT`, and `ENTRY_STOP_PRICE_BUFFER`.

Like the scale scanner, this is evidence tooling only. A positive prefilter row
is just a setup-variant survivor for independent validation; it is not approval
to change live paper parameters or `PAPER_APPROVED_STRATEGIES`.
By default it writes under
`/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs`.

For broader research, `SECOND_STRATEGY_SETUP_VARIANT_MODE=grid` expands the
existing `STRATEGY_GRIDS` cartesian combinations for the selected disabled
stock candidates. This remains read-only evidence tooling; use
`SECOND_STRATEGY_SETUP_CANDIDATES` and `SECOND_STRATEGY_SETUP_MAX_VARIANTS` to
bound focused scans.

## 2026-07-08 Setup-Knob Scan Result

A full setup-knob scan completed under the shared second-strategy evidence root:

- prefilter artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T040133Z/summary.md` and `summary.json`
- validation artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T040133Z/validation/summary.md` and `summary.json`

Result: 14 variants scanned, `positive_edge_prefilter_rows=0`, validation
variants `0`, `positive_edge_validation_rows=0`, and
`promotion_approved=false`. No setup-knob variant is approved for paper
promotion, and no strategy or live paper parameter was changed.

| candidate | lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---|---:|---:|---:|---|---|
| `bb_squeeze` | `AL_bb_period` | `BB_PERIOD=10` | 245 | 1.26 | 81.79 | [-0.2283, 0.8930] | `no-evidence` |
| `ema_pullback` | `AH_ema_period` | `EMA_PERIOD=7` | 289 | 1.02 | 8.87 | [-0.4935, 0.5898] | `no-evidence` |
| `bb_squeeze` | `AN_bb_squeeze_threshold` | `BB_SQUEEZE_THRESHOLD_PCT=0.05` | 213 | 1.09 | 30.33 | [-0.5570, 0.8521] | `no-evidence` |
| `momentum` | `U_prior_high_lookback` | `PRIOR_DAY_HIGH_LOOKBACK_BARS=2` | 286 | 1.04 | 17.20 | [-0.5759, 0.7398] | `no-evidence` |
| `breakout` | `X_breakout_lookback` | `BREAKOUT_LOOKBACK_BARS=10` | 205 | 0.54 | -211.23 | [-1.6708, -0.3623] | `negative-edge` |

## 2026-07-08 EMA Grid Follow-Up

An exploratory `ema_pullback` grid pass found seed-sensitive prefilter leads
around `EMA_PERIOD=7` and `RELATIVE_VOLUME_THRESHOLD=1.5`. The top three labels
were rerun with a fresh prefilter seed:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T050456Z/summary.md` and `validation/summary.md`
- variants: `grid_004`, `grid_005`, `grid_006`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_006` | `EMA_PERIOD=7,RELATIVE_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=2.0` | 404 | 1.18 | 80.89 | [-0.1672, 0.5736] | `no-evidence` |
| `grid_005` | `EMA_PERIOD=7,RELATIVE_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=1.5` | 405 | 1.18 | 83.19 | [-0.1681, 0.6007] | `no-evidence` |
| `grid_004` | `EMA_PERIOD=7,RELATIVE_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=1.0` | 408 | 1.16 | 82.26 | [-0.2028, 0.6076] | `no-evidence` |

Conclusion: the EMA grid leads did not reproduce under a fresh prefilter seed.
No validation candidate was available, and `PAPER_APPROVED_STRATEGIES` remains
`bull_flag`.

## 2026-07-08 BB Squeeze Period-15 Grid

The first `bb_squeeze` grid slice tested `BB_PERIOD=15` across squeeze
thresholds `0.02`, `0.03`, and `0.04` and relative-volume thresholds `1.3`,
`1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T052402Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_009`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_007` | `BB_PERIOD=15,BB_SQUEEZE_THRESHOLD_PCT=0.04,RELATIVE_VOLUME_THRESHOLD=1.3` | 355 | 1.10 | 51.84 | [-0.3175, 0.6652] | `no-evidence` |
| `grid_004` | `BB_PERIOD=15,BB_SQUEEZE_THRESHOLD_PCT=0.03,RELATIVE_VOLUME_THRESHOLD=1.3` | 324 | 1.08 | 39.24 | [-0.3552, 0.6235] | `no-evidence` |
| `grid_008` | `BB_PERIOD=15,BB_SQUEEZE_THRESHOLD_PCT=0.04,RELATIVE_VOLUME_THRESHOLD=1.5` | 314 | 0.99 | -5.09 | [-0.5318, 0.5048] | `no-evidence` |

Conclusion: the `BB_PERIOD=15` slice did not produce a positive prefilter
survivor. No validation candidate was available, and `bb_squeeze` remains
unapproved.

## 2026-07-08 BB Squeeze Period-20 Grid

The second `bb_squeeze` grid slice tested `BB_PERIOD=20` across the same
squeeze and relative-volume thresholds:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T054220Z/summary.md` and `validation/summary.md`
- variants: `grid_010` through `grid_018`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_013` | `BB_PERIOD=20,BB_SQUEEZE_THRESHOLD_PCT=0.03,RELATIVE_VOLUME_THRESHOLD=1.3` | 283 | 1.34 | 128.67 | [-0.1368, 1.1037] | `no-evidence` |
| `grid_010` | `BB_PERIOD=20,BB_SQUEEZE_THRESHOLD_PCT=0.02,RELATIVE_VOLUME_THRESHOLD=1.3` | 218 | 1.36 | 105.27 | [-0.2231, 1.2829] | `no-evidence` |
| `grid_014` | `BB_PERIOD=20,BB_SQUEEZE_THRESHOLD_PCT=0.03,RELATIVE_VOLUME_THRESHOLD=1.5` | 239 | 1.25 | 79.16 | [-0.3043, 1.0778] | `no-evidence` |

Conclusion: `BB_PERIOD=20` improved total P&L versus the period-15 slice but
still did not produce a positive prefilter survivor. No validation candidate was
available, and `bb_squeeze` remains unapproved.

## Proof Visibility

`paper_proof_status.sh` now prints a separate `paper proof second strategy setup
evidence` line from `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs`.
This keeps the latest setup/grid search result visible beside the broad basket
scan result. On 2026-07-08 it reported fresh setup evidence with
`candidate_status=no_positive_prefilter_edge`, `prefilter_positive_rows=0`, and
`promotion_approved=false`.

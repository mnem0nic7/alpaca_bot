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

## 2026-07-08 BB Squeeze Period-25 Grid

The final `bb_squeeze` grid slice tested `BB_PERIOD=25`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T055850Z/summary.md` and `validation/summary.md`
- variants: `grid_019` through `grid_027`
- prefilter result: `positive_edge_prefilter_rows=4`
- validation result: `positive_edge_validation_rows=0`,
  `promotion_approved=false`

Prefilter survivors:

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_026` | `BB_PERIOD=25,BB_SQUEEZE_THRESHOLD_PCT=0.04,RELATIVE_VOLUME_THRESHOLD=1.5` | 273 | 1.51 | 168.46 | [0.1270, 1.1266] | `positive-edge` |
| `grid_025` | `BB_PERIOD=25,BB_SQUEEZE_THRESHOLD_PCT=0.04,RELATIVE_VOLUME_THRESHOLD=1.3` | 316 | 1.48 | 185.51 | [0.1228, 1.0771] | `positive-edge` |
| `grid_022` | `BB_PERIOD=25,BB_SQUEEZE_THRESHOLD_PCT=0.03,RELATIVE_VOLUME_THRESHOLD=1.3` | 263 | 1.52 | 174.92 | [0.1156, 1.2343] | `positive-edge` |
| `grid_019` | `BB_PERIOD=25,BB_SQUEEZE_THRESHOLD_PCT=0.02,RELATIVE_VOLUME_THRESHOLD=1.3` | 212 | 1.51 | 144.55 | [0.0035, 1.3916] | `positive-edge` |

Independent validation of the top three:

| lever | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `grid_025` | 423 | 1.14 | 90.09 | [-0.2446, 0.6646] | `no-evidence` |
| `grid_026` | 401 | 1.14 | 83.62 | [-0.2635, 0.6878] | `no-evidence` |
| `grid_022` | 401 | 1.08 | 49.15 | [-0.2912, 0.5410] | `no-evidence` |

Conclusion: the `BB_PERIOD=25` prefilter positives were seed-sensitive and did
not validate independently. The full `bb_squeeze` setup grid has no promotion
candidate, and `bb_squeeze` remains unapproved.

## 2026-07-08 Momentum Lookback-1 Grid

The first `momentum` grid slice tested `PRIOR_DAY_HIGH_LOOKBACK_BARS=1` across
relative-volume thresholds `1.3`, `1.5`, `1.8`, and `2.0` and ATR stop
multipliers `1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T062528Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_012`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_009` | `PRIOR_DAY_HIGH_LOOKBACK_BARS=1,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=2.0` | 379 | 1.03 | 17.77 | [-0.4185, 0.5509] | `no-evidence` |
| `grid_007` | `PRIOR_DAY_HIGH_LOOKBACK_BARS=1,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.0` | 390 | 1.08 | 50.11 | [-0.4343, 0.7085] | `no-evidence` |
| `grid_008` | `PRIOR_DAY_HIGH_LOOKBACK_BARS=1,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.5` | 380 | 1.00 | 2.53 | [-0.4682, 0.5008] | `no-evidence` |

Conclusion: the `PRIOR_DAY_HIGH_LOOKBACK_BARS=1` momentum slice did not produce
a positive prefilter survivor. No validation candidate was available, and
`momentum` remains unapproved.

## 2026-07-08 Momentum Lookback-2 Grid

The second `momentum` grid slice tested `PRIOR_DAY_HIGH_LOOKBACK_BARS=2` across
relative-volume thresholds `1.3`, `1.5`, `1.8`, and `2.0` and ATR stop
multipliers `1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T063639Z/summary.md` and `validation/summary.md`
- variants: `grid_013` through `grid_024`
- prefilter result: `positive_edge_prefilter_rows=9`
- validation result: `positive_edge_validation_rows=0`,
  `promotion_approved=false`

Prefilter survivors:

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_021` | `PRIOR_DAY_HIGH_LOOKBACK_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=2.0` | 367 | 1.65 | 268.28 | [0.2514, 1.2218] | `positive-edge` |
| `grid_019` | `PRIOR_DAY_HIGH_LOOKBACK_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.0` | 368 | 1.61 | 284.55 | [0.2482, 1.2667] | `positive-edge` |
| `grid_020` | `PRIOR_DAY_HIGH_LOOKBACK_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.5` | 367 | 1.65 | 274.80 | [0.2477, 1.2488] | `positive-edge` |

Independent validation of the top three:

| lever | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `grid_019` | 430 | 0.98 | -15.72 | [-0.5169, 0.4791] | `no-evidence` |
| `grid_020` | 430 | 0.92 | -54.21 | [-0.5715, 0.3750] | `no-evidence` |
| `grid_021` | 427 | 0.91 | -56.82 | [-0.5963, 0.3434] | `no-evidence` |

Conclusion: the `PRIOR_DAY_HIGH_LOOKBACK_BARS=2` momentum prefilter positives
were seed-sensitive and did not validate independently. No `momentum` setup
variant is approved for paper promotion, and `momentum` remains unapproved.

## 2026-07-08 VWAP Reversion Grid

The `vwap_reversion` grid tested VWAP dip thresholds `0.01`, `0.015`, `0.02`,
and `0.025` across relative-volume thresholds `1.3`, `1.5`, and `1.8` and ATR
stop multipliers `1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T065318Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_036`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_007` | `VWAP_DIP_THRESHOLD_PCT=0.01,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.0` | 110 | 1.77 | 149.53 | [-0.0533, 2.9652] | `no-evidence` |
| `grid_009` | `VWAP_DIP_THRESHOLD_PCT=0.01,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=2.0` | 110 | 1.74 | 136.58 | [-0.1241, 2.8067] | `no-evidence` |
| `grid_008` | `VWAP_DIP_THRESHOLD_PCT=0.01,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.5` | 110 | 1.73 | 136.24 | [-0.1347, 2.7999] | `no-evidence` |
| `grid_016` | `VWAP_DIP_THRESHOLD_PCT=0.015,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.0` | 87 | 1.70 | 115.72 | [-0.3261, 3.2001] | `no-evidence` |
| `grid_018` | `VWAP_DIP_THRESHOLD_PCT=0.015,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=2.0` | 87 | 1.66 | 102.61 | [-0.4295, 3.0414] | `no-evidence` |

Conclusion: the full `vwap_reversion` setup grid did not produce a positive
prefilter survivor under the fresh prefilter seed. No validation candidate was
available, and `vwap_reversion` remains unapproved.

## 2026-07-08 Failed Breakdown Grid

The `failed_breakdown` grid tested volume ratios `1.5`, `2.0`, `2.5`, and
`3.0` across recapture buffers `0.001`, `0.002`, and `0.003` and ATR stop
multipliers `1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T071643Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_036`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_005` | `FAILED_BREAKDOWN_VOLUME_RATIO=1.5,FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT=0.002,ATR_STOP_MULTIPLIER=1.5` | 122 | 1.43 | 53.97 | [-0.1858, 1.0925] | `no-evidence` |
| `grid_001` | `FAILED_BREAKDOWN_VOLUME_RATIO=1.5,FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT=0.001,ATR_STOP_MULTIPLIER=1.0` | 159 | 1.34 | 64.72 | [-0.1868, 0.9969] | `no-evidence` |
| `grid_003` | `FAILED_BREAKDOWN_VOLUME_RATIO=1.5,FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT=0.001,ATR_STOP_MULTIPLIER=2.0` | 155 | 1.34 | 51.71 | [-0.2057, 0.8501] | `no-evidence` |
| `grid_006` | `FAILED_BREAKDOWN_VOLUME_RATIO=1.5,FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT=0.002,ATR_STOP_MULTIPLIER=2.0` | 122 | 1.40 | 48.72 | [-0.2081, 1.0195] | `no-evidence` |
| `grid_004` | `FAILED_BREAKDOWN_VOLUME_RATIO=1.5,FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT=0.002,ATR_STOP_MULTIPLIER=1.0` | 124 | 1.42 | 60.82 | [-0.2113, 1.1878] | `no-evidence` |

Conclusion: the full `failed_breakdown` setup grid did not produce a positive
prefilter survivor under the fresh prefilter seed. No validation candidate was
available, and `failed_breakdown` remains unapproved.

## 2026-07-08 EMA Pullback Full Grid

The full `ema_pullback` grid tested EMA periods `7`, `9`, `12`, and `20`
across relative-volume thresholds `1.3`, `1.5`, and `1.8` and ATR stop
multipliers `1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T075331Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_036`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_021` | `EMA_PERIOD=12,RELATIVE_VOLUME_THRESHOLD=1.3,ATR_STOP_MULTIPLIER=2.0` | 361 | 1.27 | 115.41 | [-0.0942, 0.7431] | `no-evidence` |
| `grid_027` | `EMA_PERIOD=12,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=2.0` | 292 | 1.33 | 113.72 | [-0.1077, 0.8700] | `no-evidence` |
| `grid_026` | `EMA_PERIOD=12,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.5` | 293 | 1.30 | 110.47 | [-0.1459, 0.8561] | `no-evidence` |
| `grid_036` | `EMA_PERIOD=20,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=2.0` | 247 | 1.29 | 88.11 | [-0.1507, 0.8907] | `no-evidence` |
| `grid_020` | `EMA_PERIOD=12,RELATIVE_VOLUME_THRESHOLD=1.3,ATR_STOP_MULTIPLIER=1.5` | 362 | 1.22 | 100.92 | [-0.1653, 0.7233] | `no-evidence` |

Conclusion: the full `ema_pullback` setup grid did not produce a positive
prefilter survivor under the fresh full-grid seed. No validation candidate was
available, and `ema_pullback` remains unapproved.

## 2026-07-08 ORB Full Grid

The full `orb` grid tested opening-range bars `1`, `2`, `3`, and `4` across
relative-volume thresholds `1.3`, `1.5`, and `1.8` and ATR stop multipliers
`1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T093256Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_036`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_016` | `ORB_OPENING_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.0` | 502 | 1.22 | 183.53 | [-0.1469, 0.9665] | `no-evidence` |
| `grid_018` | `ORB_OPENING_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=2.0` | 498 | 1.26 | 203.81 | [-0.1523, 0.9819] | `no-evidence` |
| `grid_017` | `ORB_OPENING_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.5` | 498 | 1.25 | 196.68 | [-0.1668, 0.9639] | `no-evidence` |
| `grid_013` | `ORB_OPENING_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=1.0` | 520 | 1.21 | 188.23 | [-0.2125, 0.9431] | `no-evidence` |
| `grid_012` | `ORB_OPENING_BARS=2,RELATIVE_VOLUME_THRESHOLD=1.3,ATR_STOP_MULTIPLIER=2.0` | 534 | 1.18 | 156.38 | [-0.2176, 0.8546] | `no-evidence` |

Conclusion: the full `orb` setup grid did not produce a positive prefilter
survivor under the fresh full-grid seed. No validation candidate was available,
and `orb` remains unapproved.

## 2026-07-08 High Watermark Full Grid

The full `high_watermark` grid tested lookbacks `63`, `126`, and `252` days
across relative-volume thresholds `1.3`, `1.5`, `1.8`, and `2.0` and ATR stop
multipliers `1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T095828Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_036`
- prefilter result: `positive_edge_prefilter_rows=1`
- validation result: `positive_edge_validation_rows=0`,
  `promotion_approved=false`

Prefilter survivor:

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_019` | `HIGH_WATERMARK_LOOKBACK_DAYS=126,RELATIVE_VOLUME_THRESHOLD=1.8,ATR_STOP_MULTIPLIER=1.0` | 79 | 2.05 | 132.28 | [0.0270, 3.7233] | `positive-edge` |

Independent validation:

| lever | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `grid_019` | 154 | 1.23 | 52.78 | [-0.3587, 1.1024] | `no-evidence` |

Conclusion: the `high_watermark` prefilter survivor did not validate
independently. No setup variant is approved for paper promotion, and
`high_watermark` remains unapproved.

## 2026-07-08 Gap And Go Full Grid

The full `gap_and_go` grid tested gap thresholds `0.01`, `0.015`, `0.02`, and
`0.025` across gap-volume thresholds `1.5`, `2.0`, and `2.5` and ATR stop
multipliers `1.0`, `1.5`, and `2.0`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T102418Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_036`
- prefilter result: `positive_edge_prefilter_rows=4`
- validation result: `positive_edge_validation_rows=0`,
  `promotion_approved=false`

Prefilter survivors:

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_002` | `GAP_THRESHOLD_PCT=0.01,GAP_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=1.5` | 51 | 2.30 | 62.36 | [0.0546, 2.4642] | `positive-edge` |
| `grid_011` | `GAP_THRESHOLD_PCT=0.015,GAP_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=1.5` | 51 | 2.30 | 62.36 | [0.0546, 2.4642] | `positive-edge` |
| `grid_003` | `GAP_THRESHOLD_PCT=0.01,GAP_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=2.0` | 51 | 2.30 | 62.03 | [0.0400, 2.4578] | `positive-edge` |
| `grid_012` | `GAP_THRESHOLD_PCT=0.015,GAP_VOLUME_THRESHOLD=1.5,ATR_STOP_MULTIPLIER=2.0` | 51 | 2.30 | 62.03 | [0.0400, 2.4578] | `positive-edge` |

Independent validation of the top three:

| lever | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `grid_012` | 99 | 1.22 | 27.96 | [-0.5272, 1.0758] | `no-evidence` |
| `grid_002` | 100 | 1.17 | 23.17 | [-0.5630, 1.0377] | `no-evidence` |
| `grid_011` | 100 | 1.17 | 23.17 | [-0.5630, 1.0377] | `no-evidence` |

Conclusion: the `gap_and_go` prefilter positives did not validate
independently. No setup variant is approved for paper promotion, and
`gap_and_go` remains unapproved.

## 2026-07-08 Breakout Full Grid

The full `breakout` grid tested lookback bars `15`, `20`, `25`, and `30`
across relative-volume thresholds `1.3`, `1.5`, `1.8`, and `2.0` and daily
SMA periods `10`, `20`, and `30`:

- artifacts: `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260708T105011Z/summary.md` and `validation/summary.md`
- variants: `grid_001` through `grid_048`
- result: `positive_edge_prefilter_rows=0`, validation variants `0`,
  `promotion_approved=false`

| lever | override | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---|---:|---:|---:|---|---|
| `grid_027` | `BREAKOUT_LOOKBACK_BARS=25,RELATIVE_VOLUME_THRESHOLD=1.3,DAILY_SMA_PERIOD=30` | 220 | 1.08 | 24.75 | [-0.4435, 0.6828] | `no-evidence` |
| `grid_015` | `BREAKOUT_LOOKBACK_BARS=20,RELATIVE_VOLUME_THRESHOLD=1.3,DAILY_SMA_PERIOD=30` | 235 | 1.02 | 7.42 | [-0.5426, 0.6091] | `no-evidence` |
| `grid_038` | `BREAKOUT_LOOKBACK_BARS=30,RELATIVE_VOLUME_THRESHOLD=1.3,DAILY_SMA_PERIOD=20` | 226 | 0.97 | -8.88 | [-0.5979, 0.5468] | `no-evidence` |
| `grid_003` | `BREAKOUT_LOOKBACK_BARS=15,RELATIVE_VOLUME_THRESHOLD=1.3,DAILY_SMA_PERIOD=30` | 268 | 0.94 | -25.62 | [-0.6174, 0.4304] | `no-evidence` |
| `grid_039` | `BREAKOUT_LOOKBACK_BARS=30,RELATIVE_VOLUME_THRESHOLD=1.3,DAILY_SMA_PERIOD=30` | 218 | 0.97 | -10.35 | [-0.6397, 0.5283] | `no-evidence` |

Conclusion: the full `breakout` setup grid did not produce a positive
prefilter survivor under the fresh full-grid seed. No validation candidate was
available, and `breakout` remains unapproved.

## Proof Visibility

`paper_proof_status.sh` now prints a separate `paper proof second strategy setup
evidence` line from `/var/lib/alpaca-bot/nightly/second_strategy/setup_knobs`.
This keeps the latest setup/grid search result visible beside the broad basket
scan result. On 2026-07-08 it reported fresh setup evidence with
`candidate_status=no_positive_prefilter_edge`, `prefilter_positive_rows=0`,
`validation_positive_rows=0`, and `promotion_approved=false`.

## 2026-07-08 Live Evidence Refresh

The latest broad basket artifact now points at:

```text
/var/lib/alpaca-bot/nightly/second_strategy/20260708T193053Z/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T193053Z/validation/summary.md
```

An initial validation at starting equity `68995.52` produced a borderline
positive `ema_pullback` contribution with candidate CI low `0.0007`. Re-running
the same validation path against current broker equity `69006.57` invalidated
that edge:

| candidate | scale | candidate trades | candidate P&L | candidate 95% CI mean/trade | candidate p(mean<=0) | verdict |
|---|---:|---:|---:|---|---:|---|
| `ema_pullback` | 0.50 | 292 | 179.74 | [-0.0075, 1.2366] | 0.0290 | `no-evidence` |

The refreshed validation summary includes `candidate_count=1` and
`candidate_names=["ema_pullback"]`, reports `validation_positive_rows=0`, and
sets `promotion_approved=false`. The current proof status therefore reports
`candidate_status=no_positive_validation_edge`,
`validated_unapproved_stock_candidates=none`, and promotion action
`status=none`.

No strategy, paper approval allowlist, or live paper parameter was changed by
this evidence refresh. Keep `PAPER_APPROVED_STRATEGIES=bull_flag`; no second
strategy is currently approved for paper promotion.

Follow-up: the basket scanner now inherits `starting_equity` from a supplied
prefilter summary unless an operator explicitly sets
`SECOND_STRATEGY_STARTING_EQUITY`. This keeps resume validations tied to the
same equity baseline as the prefilter artifact instead of drifting with live
broker equity.

Follow-up: the basket and setup-knob scanners now publish replay reports,
JSONL, stderr, and summary files through temp-file replacement. In-progress or
failed reruns should no longer expose zero-byte or partially-written evidence
artifacts over the last completed result.

## 2026-07-09 Log-Recovery Validation State

After recovering the latest validation log/artifact state, proof status now
points at:

```text
/var/lib/alpaca-bot/nightly/second_strategy/manual_log_recovery_20260709T014126Z/validation/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/manual_log_recovery_20260709T014126Z/validation/summary.json
```

The independent validation used the same
`second-strategy-independent-validation` seed, 160 scenarios, 2 bps/side
slippage, K=1, and starting equity `68995.52`. It validated only
`ema_pullback`, across confidence scales `0.10`, `0.25`, and `0.50`.

| candidate | scale | candidate trades | candidate P&L | candidate 95% CI mean/trade | candidate p(mean<=0) | candidate verdict | basket verdict |
|---|---:|---:|---:|---|---:|---|---|
| `ema_pullback` | 0.10 | 292 | `$150.76` | [0.0707, 1.0662] | 0.0090 | `positive-edge` | `no-evidence` |
| `ema_pullback` | 0.25 | 292 | `$188.21` | [0.0326, 1.2629] | 0.0190 | `positive-edge` | `no-evidence` |
| `ema_pullback` | 0.50 | 292 | `$179.74` | [-0.0075, 1.2366] | 0.0290 | `no-evidence` | `no-evidence` |

The proof status therefore reports
`candidate_status=validated_stock_candidate_unapproved`,
`validated_unapproved_stock_candidates=ema_pullback`, and
`promotion_action_status=ready_needs_approval_marker`.

This is not approval to change `PAPER_APPROVED_STRATEGIES`, enable the strategy,
or deploy. It means the remaining diversification blocker is now an explicit
operator approval gate for `ema_pullback`.

## Disabled-Candidate Decision Dry Run

Before any approval marker exists, the disabled candidate was evaluated through
the same read-only decision dry-run path that promotion uses, with disabled
strategy evaluation explicitly allowed:

```bash
PAPER_DECISION_DRY_RUN_STRATEGY=ema_pullback \
PAPER_DECISION_DRY_RUN_ALLOW_DISABLED=true \
PAPER_DECISION_DRY_RUN_REQUIRE_ACCEPTED=false \
PAPER_DECISION_DRY_RUN_SESSION_DATE=2026-07-08 \
  ./scripts/paper_decision_dry_run.sh /etc/alpaca_bot/alpaca-bot.env
```

Result:

```text
paper decision dry run ok: strategy=ema_pullback strategy_disabled=true allow_disabled=true as_of=2026-07-08T15:30:00-04:00 active=977 ignored=9 fractionable=975 intraday=977/977 completed_intraday=977/977 daily=977/977 thin_completed_lt10=0 decision_records=950 accepted=1 rejected=19 skipped_no_signal=930 entry_intents=1 reject_stages=capacity:19 reject_reasons=capacity_full:19 max_open_positions=1 equity=69006.55 sample=XP:25.974@15.44 sample_times=10:30,11:30,12:30,13:30,14:30,15:30 evaluations=6 min_decision_records=906 max_accepted=1 max_entry_intents=1
```

Conclusion: the disabled `ema_pullback` strategy can evaluate cleanly against
the active paper watchlist and produce an entry intent in the promotion dry-run
path. This removes an operational wiring concern, but it still does not grant
approval. Do not write the approval marker or promote the strategy without
explicit operator approval.

## Basket Proof-Horizon Prefilter

Before treating approval as enough to clear the paper-proof path, a read-only
`bull_flag + ema_pullback` basket proof-horizon sample was run against the
actual proof gates. The diagnostic used 160 scenarios, seed
`bull-flag-ema-proof-horizon-prefilter-20260709`, 2 bps/side slippage, K=1, and
`ema_pullback` confidence scale `0.10`.

| metric | result |
|---|---:|
| trades | 375 |
| total P&L | `$-30.03` |
| active trade days | 226 |
| historical starts checked | 276 |
| starts passing all proof gates | 22 |
| starts not proven by data end | 254 |
| eventual pass rate | 7.97% |
| first-threshold pass rate | 4.25% |

Terminal blockers were still dominated by `profit_factor` (251 starts),
`positive_pnl` (239), and `eod_loss_share` (167). The sample also had 17 starts
blocked by insufficient trades and 4 by insufficient active days.

Conclusion: `ema_pullback` may be operationally wired, but the sampled
`bull_flag + ema_pullback` basket does not currently solve the proof horizon.
This is not approval to promote or enable the strategy, and no live paper
configuration was changed.

Follow-up: the proof-horizon sample was published under
`/var/lib/alpaca-bot/nightly/second_strategy/latest_proof_horizon`. Proof
status now consumes that summary, reports
`proof_horizon_status=failed` with `proof_horizon_detail=total_pnl_below_gate`,
and suppresses the approval quick command as `review_proof_horizon` while this
negative proof-horizon evidence is current.

Follow-up: proof-horizon summaries now carry explicit `confidence_scales`.
The current published summary records `ema_pullback=0.10`, and proof status
checks that this scale matches the promotion candidate before using the horizon
result. A horizon artifact for a different scale now reports a mismatch instead
of gating approval for the wrong candidate row.

## 2026-07-09 Proof-Horizon Automation

The second-strategy basket scanner now runs the promotion-candidate proof
horizon automatically after independent validation finds a promotable stock
row. The scanner uses the same validation-row ordering as the promotion/status
path, writes the read-only `proof-horizon-basket` report under the scan output
directory, and advances `/var/lib/alpaca-bot/nightly/second_strategy/latest_proof_horizon`
only after a complete `summary.json` exists.

Defaults mirror the live scale proof gates: 160 scenarios, 30 trades, `$0.01`
minimum P&L, 5 active days, 1.20 profit factor, 0.50 max single-win P&L share,
and 0.50 max EOD-loss share. The automation does not approve or enable any
strategy; it prevents future validated candidates from looking actionable
until the basket proof horizon is fresh for the same candidate scale.

Follow-up: proof status now also requires the second-strategy basket proof
horizon to pass on at least 50% of historical start dates before promotion
becomes actionable. A candidate with positive total proof-horizon P&L but a
thin eventual pass rate now reports
`proof_horizon_detail=eventual_pass_rate_below_gate`.

Follow-up: proof-horizon automation now evaluates every independently validated
stock row in validation rank order instead of stopping after the strongest
isolated candidate. Each candidate-scale pair receives a separate immutable
artifact. The published aggregate selects the first candidate that clears the
full basket horizon, or retains the top-ranked failure when none clear it, and
records candidate and passing counts for proof status and the dashboard. This
prevents a weak basket fit from hiding a lower-ranked but better diversifier;
it still cannot approve or enable a strategy.

Follow-up: option candidates are no longer treated as replay-supported from a
single current-session snapshot. Replay readiness now requires at least five
snapshot sessions, matching the proof active-day floor. On 2026-07-09 the
ledger correctly reports `replay_status=insufficient_sessions`, with two of
five required sessions, so the twelve option families are excluded from the
broad scan instead of producing zero-trade rows. Once coverage matures, the
scanner freezes every available session at 15-minute decision boundaries and
the basket CLI samples only symbols represented in that point-in-time ledger.
This preserves usable marks across days without copying every intrabar poll.

Follow-up: proof status now verifies validation lineage by resolving the
validation summary's `prefilter_summary_json` against the currently published
prefilter summary. Both current broad and setup evidence chains report
`validation_prefilter_lineage_status=ok`; a missing or cross-generation
reference now makes the evidence invalid before any promotion action can be
offered.

Follow-up: observation-only option collection now records one successful chain
snapshot per 15-minute decision boundary from 10:00 through the configured
flatten time. Duplicate supervisor cycles in the same bar are suppressed,
empty captures retry, and both the snapshot payload and replay ledger use the
decision-boundary timestamp. This replaces the earlier one-snapshot-per-day
behavior, which could not support point-in-time intraday option replay.

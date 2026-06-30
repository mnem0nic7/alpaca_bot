# Bull Flag Shape Recheck

Date: 2026-06-30

Purpose: recheck bull-flag pattern-shape thresholds under the current paper
proof posture after promoting `MAX_LOSS_PER_TRADE_DOLLARS=20.0` and
`ATR_PERIOD=20`.

Current live proof context:

- Active paper watchlist scenarios: 980 enabled, non-ignored symbols
- Strategy: `bull_flag`
- Slippage: 2 bps per side
- Max open positions: 4
- Starting equity override: `$68,986.01`
- Proof gate: 10 closed trades and cumulative P&L >= `$0.01`

A deterministic 240-symbol active-watchlist sample screened the shape
thresholds before spending a full active-universe replay.

| Candidate | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high | Max single loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| current: min run 0.02, range 0.5, volume ratio 0.6 | 383 | `$676.00` | 1.9886 | 4.8428 | 75.20% | 0.9577 | 2.5808 | `-$20.07` |
| min run 0.015 | 449 | `$601.77` | 1.7322 | 3.9379 | 73.94% | 0.6423 | 2.0617 | `-$26.00` |
| min run 0.03 | 285 | `$530.02` | 1.9557 | 4.7427 | 76.14% | 0.8946 | 2.8061 | `-$20.07` |
| range 0.4 | 375 | `$612.91` | 1.9029 | 4.5592 | 74.93% | 0.7846 | 2.4748 | `-$20.07` |
| range 0.6 | 388 | `$697.38` | 1.9909 | 4.8637 | 75.26% | 0.9837 | 2.6751 | `-$20.07` |
| volume ratio 0.5 | 322 | `$579.12` | 2.0410 | 4.5342 | 76.09% | 0.9367 | 2.6745 | `-$20.07` |
| volume ratio 0.7 | 439 | `$656.28` | 1.8002 | 4.3230 | 74.03% | 0.7635 | 2.2978 | `-$20.07` |

Only `BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.6` improved the sample enough to
justify a full active-universe replay.

Full active-universe replay:

| Range pct | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high | Max single loss |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5, current | 1122 | `$1,283.97` | 1.5682 | 4.6039 | 72.46% | 0.6894 | 1.6014 | `-$20.07` |
| 0.6 | 1126 | `$1,235.27` | 1.5291 | 4.2598 | 72.11% | 0.6406 | 1.5568 | `-$20.07` |

Proof-horizon follow-up for range `0.6`:

| Metric | Value |
| --- | ---: |
| Historical starts checked | 269 |
| Starts eventually reaching proof gate | 266 |
| Starts not proven by data end | 3 |
| Eventual pass rate | 98.88% |
| First-threshold pass rate | 62.17% |
| First-threshold failures later recovered | 100 |
| Median sessions to proof pass | 3 |
| P90 sessions to proof pass | 15 |
| P95 sessions to proof pass | 22 |
| Slowest observed pass | 30 |
| Active trade days | 239 |
| Worst active day | `-$48.22` |
| P05 active day | `-$27.39` |
| Loss-day rate | 37.24% |

Decision:

Do not promote any bull-flag shape threshold. The only sampled improvement,
`BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.6`, failed the full active-universe check:
it added four trades but weakened total P&L, profit factor, annualized Sharpe,
win rate, CI lower bound, CI upper bound, worst active day, and p05 active day.
Keep `BULL_FLAG_MIN_RUN_PCT=0.02`,
`BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.5`, and
`BULL_FLAG_CONSOLIDATION_VOLUME_RATIO=0.6`.

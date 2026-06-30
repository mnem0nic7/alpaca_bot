# Bull Flag Relative Volume 1.8 Recheck

Date: 2026-06-30

Purpose: recheck whether loosening `RELATIVE_VOLUME_THRESHOLD` from `2.0` to
`1.8` improves the current paper proof posture after promoting
`MAX_LOSS_PER_TRADE_DOLLARS=20.0` and `ATR_PERIOD=20`.

Current live proof context:

- Active paper watchlist scenarios: 980 enabled, non-ignored symbols
- Strategy: `bull_flag`
- Slippage: 2 bps per side
- Max open positions: 4
- Starting equity override: `$68,986.01`
- Proof gate: 10 closed trades and cumulative P&L >= `$0.01`

The first screen used a deterministic 240-symbol sample. The baseline was weak
on that sample, and `RELATIVE_VOLUME_THRESHOLD=1.8` was the only variant that
improved P&L, Sharpe, and CI floor enough to justify a full active-universe
check.

Full active-universe replay:

| Relative volume threshold | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high | Max single loss |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2.0, current | 1122 | `$1,283.97` | 1.5682 | 4.6039 | 72.46% | 0.6894 | 1.6014 | `-$20.07` |
| 1.8 | 1265 | `$1,342.99` | 1.5282 | 4.6714 | 72.02% | 0.6293 | 1.4811 | `-$20.08` |

Proof-horizon follow-up for `RELATIVE_VOLUME_THRESHOLD=1.8`:

| Metric | Value |
| --- | ---: |
| Historical starts checked | 269 |
| Starts eventually reaching proof gate | 265 |
| Starts not proven by data end | 4 |
| Eventual pass rate | 98.51% |
| First-threshold pass rate | 62.55% |
| First-threshold failures later recovered | 98 |
| Median sessions to proof pass | 3 |
| P90 sessions to proof pass | 14 |
| P95 sessions to proof pass | 21 |
| Slowest observed pass | 32 |
| Active trade days | 242 |
| Worst active day | `-$44.09` |
| P05 active day | `-$24.11` |
| Loss-day rate | 35.95% |

Decision:

Do not promote `RELATIVE_VOLUME_THRESHOLD=1.8`. It improves raw P&L, trade
count, Sharpe, first-threshold proof rate, p90 proof speed, worst active day,
and loss-day rate. However, it weakens the more important robustness metrics:
profit factor, win rate, CI lower bound, CI upper bound, eventual proof pass
rate, starts not proven by data end, and slowest observed proof pass. The
current `RELATIVE_VOLUME_THRESHOLD=2.0` remains the better paper proof posture.

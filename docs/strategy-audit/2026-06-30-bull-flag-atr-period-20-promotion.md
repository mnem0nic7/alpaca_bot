# Bull Flag ATR Period 20 Promotion

Date: 2026-06-30

Purpose: test whether the current audited paper proof posture benefits from a
longer ATR lookback after promoting `MAX_LOSS_PER_TRADE_DOLLARS=20.0`.

Scenario set:

- Active paper watchlist scenarios: 980
- Strategy: `bull_flag`
- Slippage: 2 bps per side
- Max open positions: 4
- Starting equity override: `$68,986.01`
- Proof gate: 10 closed trades and cumulative P&L >= `$0.01`

| ATR period | Trades | P&L | Profit factor | Annualized Sharpe | CI low | Eventual pass | First-threshold pass | P95 sessions | Slowest sessions | Worst active day | P05 active day |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 14, current | 1121 | `$1,271.89` | 1.5639 | 4.5671 | 0.6801 | 98.88% | 62.17% | 21 | 30 | `-$46.24` | `-$23.97` |
| 20 | 1122 | `$1,283.97` | 1.5682 | 4.6039 | 0.6894 | 98.88% | 61.80% | 21 | 30 | `-$44.56` | `-$24.45` |

Decision:

Promote the audited paper proof posture to `ATR_PERIOD=20`. The candidate
slightly improves total P&L, profit factor, Sharpe, CI lower bound, trade count,
loss-day rate, and worst active day while preserving the same eventual proof
pass rate, p95 proof horizon, and slowest proof horizon. The first-threshold
pass rate and p05 active day are slightly weaker, so this is a profitability
promotion rather than a proof-speed promotion.

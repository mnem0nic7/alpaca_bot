# Bull Flag Max-Loss Cap 20 Promotion

Date: 2026-06-29

Purpose: test whether the audited paper proof posture was too tightly capped at
`MAX_LOSS_PER_TRADE_DOLLARS=10.0` after the post-close paper session finished
with 6 scoreable trades and partial P&L of `-$5.16`.

Scenario set:

- Active paper watchlist scenarios: 980
- Strategy: `bull_flag`
- Slippage: 2 bps per side
- Max open positions: 4
- Starting equity override for proof/downside check: `$68,986.01`
- Proof gate: 10 closed trades and cumulative P&L >= `$0.01`

All-period replay:

| Cap | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | Max DD | Max loss streak |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `$10` | 1066 | `$558.90` | 1.5064 | 4.1690 | 71.76% | 0.08% | 5 |
| `$20` | 1121 | `$1,271.89` | 1.5639 | 4.5671 | 72.44% | 0.21% | 6 |
| Uncapped | 1165 | `$16,113.22` | 1.5492 | 4.4119 | 72.02% | 1.32% | 5 |

Proof-horizon and downside check:

| Cap | Eventual pass rate | Not proven | First-threshold pass rate | Median sessions | P95 sessions | Slowest sessions | Worst active day | P05 active day | Max single loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `$10` | 97.03% | 8 | 59.18% | 4 | 43 | 56 | `-$22.97` | `-$10.71` | `-$10.04` |
| `$20` | 98.88% | 3 | 62.17% | 3 | 21 | 30 | `-$46.24` | `-$23.97` | `-$20.07` |

Decision:

Promote the audited paper proof posture to
`MAX_LOSS_PER_TRADE_DOLLARS=20.0`. The `$20` cap improves total P&L, profit
factor, Sharpe, trade count, eventual proof pass rate, first-threshold pass
rate, median sessions to pass, p95 sessions to pass, and slowest sessions to
pass. Downside approximately doubles as expected from the larger dollar cap, but
remains bounded and materially below the uncapped drawdown profile.

Do not remove the dollar cap. The uncapped run has much higher nominal P&L but
worse profit factor and Sharpe than `$20`, and max drawdown increases to 1.32%.

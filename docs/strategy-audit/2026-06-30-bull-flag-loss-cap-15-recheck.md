# Bull Flag Loss Cap 15 Recheck

Date: 2026-06-30

Purpose: test whether lowering `MAX_LOSS_PER_TRADE_DOLLARS` from `$20` to
`$15` improves the current paper proof posture after promoting `ATR_PERIOD=20`.

Current live proof context:

- Active paper watchlist scenarios: 980 enabled, non-ignored symbols
- Strategy: `bull_flag`
- Slippage: 2 bps per side
- Max open positions: 4
- Starting equity override: `$68,986.01`
- Proof gate: 10 closed trades and cumulative P&L >= `$0.01`

The first screen used a deterministic 240-symbol active-watchlist sample. Wider
caps improved nominal P&L but steadily weakened the CI lower bound and increased
single-trade downside.

| Cap | Trades | P&L | Profit factor | Annualized Sharpe | CI low | Max single loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `$15` | 359 | `$210.68` | 1.3451 | 2.1301 | -0.0460 | `-$14.94` |
| `$20`, current | 369 | `$269.51` | 1.3205 | 2.0083 | -0.0733 | `-$19.99` |
| `$25` | 375 | `$335.40` | 1.3075 | 1.9169 | -0.1087 | `-$25.05` |
| `$30` | 376 | `$408.71` | 1.3104 | 1.9495 | -0.1397 | `-$30.11` |
| uncapped | 383 | `$2,419.28` | 1.3432 | 2.0551 | -0.6446 | `-$177.78` |

Because the `$15` cap improved the sample's risk-adjusted metrics and reduced
the worst single-trade loss, it received a full active-universe replay.

Full active-universe replay:

| Cap | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high | Max single loss |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `$20`, current | 1122 | `$1,283.97` | 1.5682 | 4.6039 | 72.46% | 0.6894 | 1.6014 | `-$20.07` |
| `$15` | 1100 | `$917.94` | 1.5444 | 4.4256 | 72.09% | 0.4980 | 1.1769 | `-$15.05` |

Proof-horizon follow-up for `$15`:

| Metric | Value |
| --- | ---: |
| Historical starts checked | 269 |
| Starts eventually reaching proof gate | 265 |
| Starts not proven by data end | 4 |
| Eventual pass rate | 98.51% |
| First-threshold pass rate | 61.05% |
| First-threshold failures later recovered | 102 |
| Median sessions to proof pass | 3 |
| P90 sessions to proof pass | 14 |
| P95 sessions to proof pass | 20 |
| Slowest observed pass | 30 |
| Active trade days | 239 |
| Worst active day | `-$32.52` |
| P05 active day | `-$18.80` |
| Loss-day rate | 38.49% |

Decision:

Do not promote `MAX_LOSS_PER_TRADE_DOLLARS=15.0`. The lower cap improves
absolute downside and p95 proof speed, but it weakens total P&L, profit factor,
annualized Sharpe, win rate, CI lower bound, CI upper bound, eventual proof
pass rate, first-threshold proof rate, and active-trade-day count. The current
`$20` cap remains the better paper proof posture.

Do not widen the cap based on the sample ladder. Wider caps and uncapped sizing
raise nominal P&L by accepting weaker confidence floors and larger single-trade
losses, which is the wrong tradeoff while the live paper proof is still pending.

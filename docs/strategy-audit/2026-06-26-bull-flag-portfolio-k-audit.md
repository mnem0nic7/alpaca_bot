# Bull Flag Portfolio K Audit - 2026-06-26

Command:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 1 \
  --max-open-positions 2 \
  --max-open-positions 3
```

Runtime: 19m28.918s.

Scenarios: 999 nightly 252d scenarios, pooled into one shared equity pool.

Effective paper proof posture: `MAX_OPEN_POSITIONS=2`, `REPLAY_SLIPPAGE_BPS=2`,
`ENABLE_VWAP_ENTRY_FILTER=true`, `ENABLE_VIX_FILTER=false`, and
`ENABLE_SECTOR_FILTER=false`. The portfolio replay uses the engine's symbol and
VWAP inputs, but does not pass VIX/sector `market_context`; those gates must stay
off for this paper proof until they are included in a separate positive-edge
portfolio audit.

| K | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 463 | 69.1% | 1.12 | 1459.08 | 3.1514 | 0.81 | [-4.4898, 11.1166] | 0.1915 | no-evidence |
| 2 | 913 | 68.8% | 1.24 | 5524.58 | 6.0510 | 2.08 | [0.4345, 11.4808] | 0.0170 | positive-edge |
| 3 | 1290 | 68.6% | 1.19 | 6478.36 | 5.0220 | 1.90 | [0.3853, 9.5642] | 0.0170 | positive-edge |

Decision: keep paper deployment at `MAX_OPEN_POSITIONS=2`. K=3 has higher
total historical P&L, but K=2 has the stronger profit factor, mean/trade,
annualized Sharpe, and CI floor. The current paper objective is to establish
profitable closed paper trades with the most robust available audited posture,
not to maximize in-sample turnover.

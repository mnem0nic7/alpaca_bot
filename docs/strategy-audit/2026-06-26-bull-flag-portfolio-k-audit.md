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

Runtime: 19m8.814s after replay/live sizing alignment.

Scenarios: 999 nightly 252d scenarios, pooled into one shared equity pool.

Effective paper proof posture: `MAX_OPEN_POSITIONS=2`, `REPLAY_SLIPPAGE_BPS=2`,
`ENABLE_VWAP_ENTRY_FILTER=true`, `ENABLE_VIX_FILTER=false`, and
`ENABLE_SECTOR_FILTER=false`. The portfolio replay validates regular-session
entries only, so `EXTENDED_HOURS_ENABLED` must stay false for this paper proof
until pre-market and after-hours trading are included in a separate positive-edge
portfolio audit. The portfolio replay uses the engine's symbol and VWAP inputs,
but does not pass VIX/sector `market_context`; those gates must stay off for this
paper proof until they are included in a separate positive-edge portfolio audit.
Replay carries the engine-selected entry quantity into the simulated fill,
matching live paper's submitted order quantity.

| K | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 463 | 69.1% | 1.12 | 1459.46 | 3.1522 | 0.81 | [-4.4835, 11.1084] | 0.1920 | no-evidence |
| 2 | 913 | 68.8% | 1.23 | 5507.00 | 6.0318 | 2.07 | [0.4142, 11.4597] | 0.0170 | positive-edge |
| 3 | 1290 | 68.6% | 1.19 | 6465.00 | 5.0116 | 1.90 | [0.3868, 9.5528] | 0.0175 | positive-edge |

Decision: keep paper deployment at `MAX_OPEN_POSITIONS=2`. K=3 has higher
total historical P&L, but K=2 has the stronger profit factor, mean/trade,
annualized Sharpe, and CI floor. The current paper objective is to establish
profitable closed paper trades with the most robust available audited posture,
not to maximize in-sample turnover.

## Live Confidence-Floor Sizing Check

Current paper has no closed `bull_flag` history yet, so the confidence score is
the operator-set floor `0.25`. With the latest paper equity baseline
`$68,991.18`, live sizing behaves like a portfolio replay starting around
`$17,247.80`.

Command:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 2 \
  --starting-equity 17247.795
```

Result:

| K | starting equity | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 2 | 17,247.80 | 916 | 68.6% | 1.22 | 860.26 | 0.9391 | 1.93 | [0.0418, 1.8554] | 0.0180 | positive-edge |

Decision: keep the paper confidence floor at `0.25` while `bull_flag` has no
paper trade history. The floor-sized replay remains positive-edge after 2 bps
slippage, so raising the floor just to match the 100k audit would add risk
without being required for the paper profit proof.

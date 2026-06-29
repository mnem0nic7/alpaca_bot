# Bull Flag 3R Profit Target Promotion - 2026-06-29

Purpose: test whether enabling a fixed 3R profit target improves the current
paper proof posture before the 2026-06-29 market session.

Baseline live posture before the check:

- `TRADING_MODE=paper`
- active strategy: `bull_flag`
- active scenarios: `980`
- `MAX_OPEN_POSITIONS=4`
- `REPLAY_SLIPPAGE_BPS=2.0`
- `ENABLE_PROFIT_TARGET=false`
- proof gate: at least `10` closed trades and `$0.01` cumulative P&L

Read-only portfolio audit command:

```bash
docker compose --env-file /etc/alpaca_bot/alpaca-bot.env -f deploy/compose.yaml run -T --rm \
  -e ENABLE_PROFIT_TARGET=true \
  -e PROFIT_TARGET_R=3.0 \
  --entrypoint python nightly \
  -m alpaca_bot.replay.cli portfolio-audit \
    --scenario-dir /data/active_scenarios \
    --strategy bull_flag \
    --slippage-bps 2 \
    --max-open-positions 4 \
    --starting-equity 17247.795
```

| posture | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---:|---|---|
| baseline, no target | 1,235 | `$2,043.30` | 1.38 | 3.63 | [0.8415, 2.4886] | positive-edge |
| 3R profit target | 1,235 | `$2,087.64` | 1.39 | 3.69 | [0.8687, 2.5407] | positive-edge |

Proof-horizon follow-up for the 3R target:

| metric | value |
|---|---:|
| historical starts checked | 269 |
| starts eventually reaching proof gate | 267 |
| eventual pass rate | 99.26% |
| first-threshold pass rate | 61.80% |
| first-threshold failures later recovered | 102 |
| median sessions to proof pass | 3 |
| p90 sessions to proof pass | 16 |
| p95 sessions to proof pass | 24 |
| slowest observed pass | 38 |

Decision: promote `ENABLE_PROFIT_TARGET=true` and `PROFIT_TARGET_R=3.0` for
the paper proof posture. The candidate preserves trade count and eventual proof
pass rate while slightly improving after-cost P&L, profit factor, Sharpe, CI
floor, and worst observed proof-pass horizon.

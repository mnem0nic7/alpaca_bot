# Bull Flag Trailing ATR 1.0 Promotion - 2026-06-29

Purpose: test whether tightening the existing trailing ATR stop improves the
current paper proof posture after the 3R profit target promotion.

Baseline live posture before the check:

- `TRADING_MODE=paper`
- active strategy: `bull_flag`
- active scenarios: `980`
- `MAX_OPEN_POSITIONS=4`
- `REPLAY_SLIPPAGE_BPS=2.0`
- `ENABLE_PROFIT_TARGET=true`
- `PROFIT_TARGET_R=3.0`
- `TRAILING_STOP_ATR_MULTIPLIER=1.5`
- proof gate: at least `10` closed trades and `$0.01` cumulative P&L

Read-only portfolio audit command:

```bash
docker compose --env-file /etc/alpaca_bot/alpaca-bot.env -f deploy/compose.yaml run -T --rm \
  -e TRAILING_STOP_ATR_MULTIPLIER=1.0 \
  --entrypoint python nightly \
  -m alpaca_bot.replay.cli portfolio-audit \
    --scenario-dir /data/active_scenarios \
    --strategy bull_flag \
    --slippage-bps 2 \
    --max-open-positions 4 \
    --starting-equity 17247.795
```

| trailing ATR multiplier | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | verdict |
|---:|---:|---:|---:|---:|---|---:|---|
| 1.0 | 1,235 | `$2,163.27` | 1.4062 | 3.7953 | [0.9253, 2.6061] | 0.0000 | positive-edge |
| 1.5, current | 1,235 | `$2,087.64` | 1.3924 | 3.6862 | [0.8687, 2.5407] | 0.0005 | positive-edge |
| 2.5 | 1,235 | `$2,087.64` | 1.3924 | 3.6862 | [0.8687, 2.5407] | 0.0005 | positive-edge |
| 3.5 | 1,235 | `$2,087.64` | 1.3924 | 3.6862 | [0.8687, 2.5407] | 0.0005 | positive-edge |

Proof-horizon follow-up for the 1.0 trailing ATR multiplier:

| metric | value |
|---|---:|
| historical starts checked | 269 |
| starts eventually reaching proof gate | 267 |
| starts not proven by data end | 2 |
| eventual pass rate | 99.26% |
| starts reaching trade threshold | 267 |
| first-threshold pass rate | 61.80% |
| first-threshold failures later recovered | 102 |
| median sessions to proof pass | 3 |
| p90 sessions to proof pass | 16 |
| p95 sessions to proof pass | 24 |
| slowest observed pass | 38 |
| active trade days | 241 |

Decision: promote `TRAILING_STOP_ATR_MULTIPLIER=1.0` for the paper proof
posture. The candidate preserves trade count and proof-horizon behavior while
improving after-cost P&L, profit factor, Sharpe, and the CI lower bound.

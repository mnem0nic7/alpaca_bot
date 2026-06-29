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

## Trailing Trigger Follow-up

After deploying the 1.0 trailing ATR multiplier, `TRAILING_STOP_PROFIT_TRIGGER_R=0.5`
was tested as a follow-up. It improved aggregate after-cost P&L, but the proof
velocity metric that matters before live paper proof weakened slightly.

| posture | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | first-threshold pass rate | slowest observed pass |
|---|---:|---:|---:|---:|---|---:|---:|
| trigger 1.0, current | 1,235 | `$2,163.27` | 1.4062 | 3.7953 | [0.9253, 2.6061] | 61.80% | 38 |
| trigger 0.5 | 1,237 | `$2,189.92` | 1.42 | 3.87 | [0.9285, 2.5893] | 60.67% | 38 |

Decision: keep `TRAILING_STOP_PROFIT_TRIGGER_R=1.0`. The 0.5R trigger has a
small CI-floor and P&L improvement, but it lowers the first-threshold pass rate
from 61.80% to 60.67%. That is not enough evidence to change the paper proof
posture immediately before the 2026-06-29 session.

## Profit Trail Distance Follow-up

After the trailing ATR promotion, the existing `PROFIT_TRAIL_PCT=0.95` became
too tight relative to the ATR trail. Looser profit-trail distances were tested
against the same active scenario universe, K=4, 2 bps slippage, 3R target, and
1.0 trailing ATR posture.

| profit trail pct | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | first-threshold pass rate | p90 pass | p95 pass | slowest pass |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 0.95, current | 1,235 | `$2,163.27` | 1.4062 | 3.7953 | [0.9253, 2.6061] | 61.80% | 16 | 24 | 38 |
| 0.975 | 1,288 | `$1,988.61` | 1.4054 | 3.8290 | [0.9010, 2.2178] | not tested | not tested | not tested | not tested |
| 0.925 | 1,229 | `$2,436.04` | 1.4697 | 4.1587 | [1.1519, 2.8339] | 59.55% | 17 | 22 | 31 |
| 0.90 | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | 59.93% | 17 | 22 | 31 |
| off | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | not tested | not tested | not tested | not tested |

Decision: promote `PROFIT_TRAIL_PCT=0.90` while keeping profit-trail enabled.
The 0.90 setting improves aggregate after-cost P&L, profit factor, Sharpe, and
CI lower bound while improving the proof-horizon tail (`p95` and slowest pass).
Its first-threshold pass rate is lower than 0.95, so the promotion is based on
the stronger profitability and better tail horizon, not on immediate first-10
trade proof velocity. Disabling the profit trail was identical to 0.90 in this
sample, but keeping a loose trail preserves an explicit profit guard.

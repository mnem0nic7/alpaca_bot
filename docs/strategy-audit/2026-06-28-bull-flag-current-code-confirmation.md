# Bull Flag Current-Code Confirmation - 2026-06-28

Purpose: re-check the deployed paper proof posture against the exact active
paper universe before the 2026-06-29 proof start.

Live posture verified before replay:

- `TRADING_MODE=paper`
- `STRATEGY_VERSION=v1-breakout`
- Only `bull_flag` enabled in `strategy_flags`
- `RELATIVE_VOLUME_THRESHOLD=2.0`
- `MAX_OPEN_POSITIONS=3`
- `ENABLE_VWAP_ENTRY_FILTER=true`
- `ENABLE_VIX_FILTER=false`
- `ENABLE_SECTOR_FILTER=false`
- `EXTENDED_HOURS_ENABLED=false`
- `PAPER_PROOF_FREEZE=true`
- Confidence floor stored at `0.25`
- Broker flat with `open_orders=0` and `open_positions=0`

Current proof status before replay:

- Proof start: `2026-06-29`
- Required closed trades: `10`
- Required cumulative P&L: `$0.01`
- Status: pending because the proof window had not started

Command:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current.jsonl
```

Result from commit `40a0923`:

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Reconfirmation after the paper-proof guardrail and deploy-freshness hardening
on 2026-06-28 used the same exact active 120-day scenario directory and the
same floor-sized proof posture:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --jsonl /tmp/alpaca-bull-flag-120d-current-7b358e6.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Decision: keep the deployed `bull_flag` paper proof posture for the 2026-06-29
proof start. The current-code exact active-universe latest-120-day replay still
shows a positive edge after 2 bps per-side slippage, with a positive confidence
interval lower bound and no live exposure before proof start.

Reconfirmation after deploy proof settle hardening at commit `ff47f2f` checked
that the live enabled, non-ignored paper watchlist still exactly matched the
latest-120-day active scenario directory:

- live active paper symbols: `980`
- scenario files: `980`
- missing active symbols from scenarios: `0`
- extra scenario symbols: `0`

The exact active-universe 120-day portfolio audit was rerun with the deployed
proof posture and floor-sized equity:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-ff47f2f.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-ff47f2f.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

A harsher 5 bps-per-side stress pass was also rerun against the same exact
active universe:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 5 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-stress-5bps-ff47f2f.md \
  --jsonl /tmp/alpaca-bull-flag-120d-stress-5bps-ff47f2f.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 412 | 74.0% | 1.65 | 939.28 | 2.2798 | 5.06 | [0.8819, 3.6206] | 0.0000 | 1283.93 | 344.64 | positive-edge |

Decision: leave production paper settings unchanged. The currently deployed
posture still clears the exact live-universe 2 bps proof replay and remains
positive-edge under a 5 bps stress replay, so there is no evidence-based reason
to alter the paper proof configuration immediately before the 2026-06-29
session.

Proof-velocity stress check at commit `0099566` compared the deployed K=3
posture against wider K=4 and K=5 alternatives under a harsher 5 bps-per-side
slippage assumption:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 5 \
  --max-open-positions 3 \
  --max-open-positions 4 \
  --max-open-positions 5 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-k345-stress-5bps-0099566.md \
  --jsonl /tmp/alpaca-bull-flag-120d-k345-stress-5bps-0099566.jsonl
```

| K | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 3 | 980 | 412 | 74.0% | 1.65 | 939.28 | 2.2798 | 5.06 | [0.8819, 3.6206] | 0.0000 | 1283.93 | 344.64 | positive-edge |
| 4 | 980 | 501 | 72.5% | 1.48 | 934.64 | 1.8655 | 4.56 | [0.5964, 3.0477] | 0.0025 | 1367.82 | 433.18 | positive-edge |
| 5 | 980 | 561 | 71.7% | 1.40 | 921.66 | 1.6429 | 4.02 | [0.4389, 2.8391] | 0.0055 | 1380.13 | 458.47 | positive-edge |

Decision: keep K=3. Wider K improves historical trade count, but it lowers
after-cost profit factor, total P&L, mean/trade, annualized Sharpe, and
confidence-interval floor under stress slippage. For paper proof, the current
K=3 posture is still the better tradeoff between proof velocity and robust
profitability.

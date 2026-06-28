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

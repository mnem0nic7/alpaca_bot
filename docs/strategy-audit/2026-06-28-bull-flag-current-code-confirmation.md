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

Daily proof-cadence replay at the configured 2 bps slippage grouped the same
exact active-universe latest-120-day replay by exit session:

| K | trades | active trade days | avg trades/active day | days with 10+ trades | profitable active days | median active sessions to 10 trades | max active sessions to 10 trades | positive P&L when 10 trades reached |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 417 | 95 | 4.39 | 2 | 63 | 3 | 5 | 67/93 |
| 4 | 505 | 95 | 5.32 | 5 | 60 | 2 | 5 | 71/93 |

Decision: still keep K=3. K=4 would likely reach the 10-trade proof threshold
about one active session sooner, but it has fewer profitable active days and
the 5 bps stress sweep shows a weaker profit factor, Sharpe, confidence floor,
and worse low-end proof-window losses. Under K=3, a one-session proof is
possible but not the base case; two to five active sessions is historically
normal, with a median of three active sessions to reach 10 closed trades.

Current-code reconfirmation after runtime-image proof-status hardening at
commit `395f950` reran the exact active-universe latest-120-day portfolio audit
with the deployed proof posture and floor-sized equity:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-395f950.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-395f950.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

The same live state also passed a weekend paper-activity dry run: readiness was
already passed for the 2026-06-29 session, the Alpaca market clock reported the
market closed, and the activity check exited cleanly with
`paper activity skipped: market closed in last 90 minutes`. This confirms the
post-open activity path remains non-disruptive before the proof window opens.

The post-close proof scripts were also dry-run directly against the live paper
broker state before the proof window opened:

```bash
./scripts/session_guard.sh /etc/alpaca_bot/alpaca-bot.env
./scripts/paper_profit_probe.sh /etc/alpaca_bot/alpaca-bot.env
```

Both exited `43` as pending because the latest completed session was
`2026-06-26`, which is before the configured `2026-06-29` proof start. Both
also confirmed broker exposure was flat (`open_orders=0`, `open_positions=0`),
so the pre-start pending path did not apply close-only.

Post profit-probe pending-guard regression at commit `4359314`, the live active
paper watchlist was compared with the exact latest-120-day replay directory:

- live active paper symbols: `980`
- scenario files: `980`
- live/scenario symbol diff: `0`

The same current-code exact active-universe portfolio audit was rerun with the
deployed proof posture and floor-sized equity:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-4359314.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-4359314.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Decision: keep the deployed paper posture unchanged. The current commit still
clears the exact live-universe 2 bps replay with positive after-cost edge, and
the live proof stack is waiting on a completed 2026-06-29-or-later proof session
rather than a configuration or exposure blocker.

Current-head confirmation at commit `f743e93` rechecked the live paper universe
against the latest-120-day replay directory before the proof window:

- live active paper symbols: `980`
- scenario files: `980`
- live/scenario symbol diff: `0`
- only enabled paper strategy: `bull_flag`
- deployed posture: `RELATIVE_VOLUME_THRESHOLD=2.0`,
  `MAX_OPEN_POSITIONS=3`, `ENABLE_VWAP_ENTRY_FILTER=true`,
  `PAPER_PROOF_FREEZE=true`, `REPLAY_SLIPPAGE_BPS=2.0`

The exact active-universe portfolio audit was rerun with the deployed proof
posture and floor-sized equity:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-f743e93.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-f743e93.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Decision: keep the deployed paper posture unchanged for the 2026-06-29 proof
start. The current head still clears the exact live-universe latest-120-day
replay with positive after-cost edge, and the proof stack remains ready but
pending a completed proof session.

Structured proof-status audit dry run:

```bash
PROOF_STATUS_FAIL_ON_ISSUES=true \
  ./scripts/run_locked_check_with_audit.sh \
    paper_proof_status \
    /var/lock/alpaca-bot-proof-status.lock \
    /etc/alpaca_bot/alpaca-bot.env \
    ./scripts/paper_proof_status.sh \
    /etc/alpaca_bot/alpaca-bot.env
```

Result before the proof window opened:

- wrapper exit: `43`
- audit status: `pending`
- audit exit code: `43`
- structured fields: `proof_status=pending`, `proof_closed_trades=0`,
  `proof_pnl=0.00`
- proof summary: `readiness=ready`, `blockers=none`,
  `reason=awaiting_completed_proof_session`

Alpaca calendar check confirmed the proof start is the next market session:
`2026-06-29`, open `09:30` ET and close `16:00` ET.

Current-head replay confirmation at commit `27fbe01`:

- live active paper symbols: `980`
- scenario files: `980`
- live/scenario symbol diff: `0`
- deployed proof posture: `bull_flag`, `MAX_OPEN_POSITIONS=3`,
  `REPLAY_SLIPPAGE_BPS=2.0`, floor-sized starting equity `$17,247.795`

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-27fbe01.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-27fbe01.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Decision: keep the deployed paper posture unchanged. Current head remains
ready for the `2026-06-29` paper proof start, with no live/scenario universe
drift and the same positive after-cost active-universe replay edge.

Scenario freshness scan before the proof start:

- exact active latest-120-day scenario files: `980`
- daily coverage max date: `2026-06-26` for all `980`
- intraday coverage max date: `2026-06-26` for all `980`
- stale scenario files: `0`
- sparse-but-current intraday files under 1000 bars: `24`

Decision: no universe change. Sparse symbols remain current, were included in
the positive-edge active-universe replay above, and the live proof stack already
requires a fresh paper-readiness pass before regular-session entries.

Current-head replay confirmation after proof-status readiness-window and env
default hardening at commit `af84933`:

- live active paper symbols: `980`
- enabled paper symbols: `986`
- ignored paper symbols: `6`
- exact active latest-120-day scenario files: `980`
- deployed proof posture: `bull_flag`, `RELATIVE_VOLUME_THRESHOLD=2.0`,
  `MAX_OPEN_POSITIONS=3`, `REPLAY_SLIPPAGE_BPS=2.0`, floor-sized starting
  equity `$17,247.795`

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-af84933.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-af84933.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Decision: keep the deployed paper posture unchanged. Current head still clears
the exact live-universe latest-120-day replay after the proof automation fixes,
so the remaining proof dependency is live paper execution on or after the
`2026-06-29` market session.

Current-head replay confirmation after active-scenario nightly hardening at
commit `f469148`:

- live active paper symbols: `980`
- exact active latest-120-day scenario files: `980`
- live/scenario symbol diff: `0`
- daily coverage max date: `2026-06-26` for all `980`
- intraday coverage max date: `2026-06-26` for all `980`
- deployed proof posture: `bull_flag`, `RELATIVE_VOLUME_THRESHOLD=2.0`,
  `MAX_OPEN_POSITIONS=3`, `REPLAY_SLIPPAGE_BPS=2.0`, floor-sized starting
  equity `$17,247.795`

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-f469148.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-f469148.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Decision: keep the deployed paper posture unchanged. The current deployed
HEAD still clears the exact active-universe latest-120-day replay with positive
after-cost edge, and the proof stack remains ready but pending a completed
`2026-06-29`-or-later paper proof session.

Current-head replay confirmation after midday readiness cron hardening at
commit `b19f905` used the exact live enabled, non-ignored proof universe from
Postgres and symlinked the matching live nightly scenarios:

- live active paper symbols: `980`
- exact active live scenario files: `980`
- missing active scenarios: `0`
- deployed proof posture: `bull_flag`, `MAX_OPEN_POSITIONS=3`,
  `REPLAY_SLIPPAGE_BPS=2.0`, floor-sized starting equity `$17,247.795`

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-proof-vWYxQs \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-active-proof-b19f905.md \
  --jsonl /tmp/alpaca-bull-flag-active-proof-b19f905.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 1033 | 71.6% | 1.42 | 1810.17 | 1.7523 | 3.61 | [0.8460, 2.6589] | 0.0000 | 2210.26 | 400.09 | positive-edge |

Decision: keep the deployed paper posture unchanged. The full live active
scenario set at current HEAD remains positive-edge after 2 bps per-side
slippage, while live proof status is still ready and pending the first
`2026-06-29`-or-later completed paper session.

Live decision-path dry run after intraday readiness smoke hardening at commit
`20f32f4` used the exact enabled, non-ignored paper watchlist from Postgres and
the production Alpaca IEX feed. The run did not persist orders, did not write
decision logs, and did not call broker order submission; it fetched bars and ran
`evaluate_cycle` in memory for `bull_flag` at `2026-06-26T15:30:00-04:00`.

- active paper symbols: `980`
- ignored paper symbols: `6`
- fractionable active symbols: `978`
- account equity used for sizing: `$68,991.18`
- completed intraday coverage: `980/980`
- daily coverage: `980/980`
- symbols with fewer than 20 completed intraday bars: `3`
  (`DJCO:8`, `NUTX:18`, `ODC:18`)
- decision records produced: `965`
- accepted entry records: `1`
- rejected records: `2`
- skipped no-signal records: `962`
- accepted intent sample: `TPB:39.62732912119471@87.05`

Decision: keep the deployed paper posture unchanged. The live decision path can
evaluate nearly the whole active universe and produces enough strategy-specific
decision evidence for the 10:25/12:00 paper-activity checks. The three thin
symbols are sparse-data names, not infrastructure failures, and the proof
status remains `ready` / `pending` with no blockers until live paper execution
starts on or after `2026-06-29`.

Current-head replay confirmation after option-exposure proof hardening at
commit `4051366` used the exact latest-120-day active proof universe:

- active paper symbols: `980`
- exact active latest-120-day scenario files: `980`
- deployed proof posture: `bull_flag`, `RELATIVE_VOLUME_THRESHOLD=2.0`,
  `MAX_OPEN_POSITIONS=3`, `REPLAY_SLIPPAGE_BPS=2.0`, floor-sized starting
  equity `$17,247.795`
- strict proof status after redeploy: `readiness=ready`, `proof=pending`,
  `blockers=none`, `warnings=none`, `proof_status_rc=43`
- exposure: local stock positions `0`, local active stock orders `0`, local
  option net-open `0`, local active option orders `0`, broker open orders `0`,
  broker open positions `0`

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
PYTHONPATH=src python3 -m alpaca_bot.replay.cli portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795 \
  --output /tmp/alpaca-bull-flag-120d-current-4051366.md \
  --jsonl /tmp/alpaca-bull-flag-120d-current-4051366.jsonl
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | frictionless P&L | cost drag | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| 980 | 417 | 74.8% | 1.75 | 1067.75 | 2.5605 | 5.83 | [1.1926, 3.8842] | 0.0000 | 1283.93 | 216.18 | positive-edge |

Decision: keep the deployed paper posture unchanged for the `2026-06-29` proof
start. The latest code and deployed proof guardrails still show positive
after-cost active-universe edge while the live system is ready, flat, and
waiting only on completed paper trades from a `2026-06-29`-or-later session.

Readiness dry-run sampling was widened after the `4051366` deploy because the
single default `15:30` sample was conservative relative to the intraday entry
path. A one-fetch, six-sample dry run against the latest completed session
(`2026-06-26`) showed that earlier regular-session samples reached the deployed
K=3 entry cap:

```bash
PAPER_DECISION_DRY_RUN_SAMPLE_TIMES=10:30,11:30,12:30,13:30,14:30,15:30 \
PAPER_DECISION_DRY_RUN_MIN_RECORDS=900 \
  ./scripts/paper_decision_dry_run.sh /etc/alpaca_bot/alpaca-bot.env
```

Result:

- active symbols: `980`
- intraday coverage: `980/980`
- daily coverage: `980/980`
- best sample: `2026-06-26T11:30:00-04:00`
- decision records at best sample: `941`
- accepted entry records at best sample: `3`
- entry intents at best sample: `3`
- minimum decision records across all six samples: `929`
- maximum accepted records across all six samples: `3`
- sample accepted intent: `DASH:18.91931662370427@182.33`

Decision: keep the trading posture unchanged, but make the readiness dry run
sample `10:30,11:30,12:30,13:30,14:30,15:30` by default. This improves proof
readiness evidence without loosening entry filters, raising risk, adding live
orders, or changing the deployed strategy parameters.

Deploy-time paper proof smoke was aligned with the same read-only multi-sample
dry-run gate at commit `673dc0e`. The deploy script now defaults its
decision dry run to the readiness strategy, `900` minimum records, and the
six-sample regular-session window. This keeps deploy output consistent with
proof readiness and avoids falling back to the single conservative `15:30`
sample during a redeploy.

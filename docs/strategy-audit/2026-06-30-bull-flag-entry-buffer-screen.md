# Bull Flag Entry Buffer Screen

Date: 2026-06-30

Purpose: check whether lowering `ENTRY_STOP_PRICE_BUFFER` would safely improve
paper proof throughput after the live 2026-06-30 paper session produced 16
accepted decisions but only 6 filled entries before the flat profit lock.

Current live context:

- Strategy: `bull_flag`
- Proof start: `2026-06-30`
- Current paper session: 6 closed trades, 4 wins, `+$10.89`
- Current proof posture: `ENTRY_STOP_PRICE_BUFFER=0.02`, `MAX_OPEN_POSITIONS=4`,
  `REPLAY_SLIPPAGE_BPS=2`, starting equity override `$68,996.87`
- Proof gate: 10 closed trades and cumulative P&L >= `$0.01`

Method:

- Read-only `proof-horizon` replay.
- Deterministic 240-symbol sample from `/var/lib/alpaca-bot/nightly/scenarios`.
- Sample seed: `entry-buffer-screen`.
- No production config change.

| Entry stop buffer | Trades | Total P&L | Eventual proof pass | First-threshold pass | P90 sessions | P95 sessions | Slowest pass | Active trade days |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `0.005` | 218 | `$293.15` | 97.78% | 71.21% | 29 | 33 | 44 | 129 |
| `0.01` | 219 | `$309.17` | 95.19% | 78.38% | 29 | 34 | 43 | 126 |
| `0.02`, current | 209 | `$262.05` | 94.81% | 75.19% | 27 | 31 | 43 | 124 |

Decision:

Do not promote an entry-buffer change from this sample. Lower buffers add trades
and improve some headline metrics, but the proof-speed metrics are mixed. The
current `0.02` buffer has the best p90 and p95 sessions-to-proof in this screen,
while `0.005` improves eventual pass rate at the cost of slower p90, p95, and
slowest-pass horizons. `0.01` improves P&L and first-threshold pass rate, but
weakens eventual pass rate and p95 horizon.

Keep the current live paper posture while the 2026-06-30 profitable session is
locked. The only follow-up worth more evidence is `0.005`, and it must be
judged against both profitability and proof-horizon speed before any deployment.
That follow-up is completed below.

## Full Scenario-Directory Follow-up

After the runtime protective-stop hardening deploy, the `0.005` follow-up was
run against the full scenario directory from the deployed image. This was still
read-only and used the same live paper proof posture except for the entry buffer
override.

Command shape:

```bash
docker compose --env-file /etc/alpaca_bot/alpaca-bot.env -f deploy/compose.yaml \
  run -T --rm \
  -v /var/lib/alpaca-bot/nightly/scenarios:/var/lib/alpaca-bot/nightly/scenarios:ro \
  -e ENTRY_STOP_PRICE_BUFFER=<candidate> \
  --entrypoint alpaca-bot-backtest admin proof-horizon \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 4 \
  --starting-equity 68996.87 \
  --min-trades 10 \
  --min-pnl 0.01 \
  --output -
```

| Entry stop buffer | Scenarios | Trades | Total P&L | Eventual proof pass | First-threshold pass | P90 sessions | P95 sessions | Slowest pass | Active trade days |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `0.005` | 999 | 705 | `$756.29` | 97.78% | 71.91% | 14 | 22 | 60 | 212 |
| `0.02`, current | 999 | 691 | `$954.41` | 98.89% | 68.16% | 19 | 45 | 58 | 212 |

Decision:

Do not promote `ENTRY_STOP_PRICE_BUFFER=0.005`. The lower buffer improves trade
count, first-threshold pass rate, and the typical proof-speed tail (`p90`/`p95`),
but it reduces total after-cost P&L by `$198.12`, weakens eventual proof pass
rate, doubles starts not proven by data end from 3 to 6, and slightly worsens
the slowest observed pass. While the live paper proof is pending, proof speed is
useful, but not at the cost of weaker profitability and lower eventual pass
reliability.

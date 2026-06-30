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
locked. If proof remains trade-count constrained after the next completed
session, the only follow-up worth a full active-universe replay is `0.005`, and
it should be judged against both profitability and proof-horizon speed before
any deployment.

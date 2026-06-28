# Bull Flag Paper Proof Velocity Check - 2026-06-28

Purpose: verify that the deployed paper proof posture is still finding enough
recent `bull_flag` entry opportunities to make progress toward the paper proof
requirement without loosening filters or increasing live paper risk before the
2026-06-29 proof start.

Live posture before the check:

- `TRADING_MODE=paper`
- `STRATEGY_VERSION=v1-breakout`
- active strategy: `bull_flag`
- `MAX_OPEN_POSITIONS=3`
- `REPLAY_SLIPPAGE_BPS=2`
- active paper symbols: `980`
- ignored paper symbols: `6`
- local exposure: `0` positions, `0` active orders
- broker exposure: `0` open orders, `0` open positions
- proof status: `readiness=ready`, `proof=pending`,
  `reason=awaiting_completed_proof_session`

The dry run is read-only. It evaluates the latest active paper watchlist across
six regular-session sample times for each recent completed market session:

```bash
for session in 2026-06-22 2026-06-23 2026-06-24 2026-06-25 2026-06-26; do
  PAPER_DECISION_DRY_RUN_SESSION_DATE="$session" \
  PAPER_DECISION_DRY_RUN_SAMPLE_TIMES=10:30,11:30,12:30,13:30,14:30,15:30 \
  PAPER_DECISION_DRY_RUN_MIN_RECORDS=900 \
    ./scripts/paper_decision_dry_run.sh /etc/alpaca_bot/alpaca-bot.env
done
```

| session | best sample ET | decision records | min records | accepted | entry intents | sample |
|---|---:|---:|---:|---:|---:|---|
| 2026-06-22 | 11:30 | 937 | 927 | 3 | 3 | `VERA:90.25533751962323@38.22` |
| 2026-06-23 | 12:30 | 936 | 930 | 3 | 3 | `ROAD:27.656209412330632@124.73` |
| 2026-06-24 | 15:30 | 953 | 941 | 3 | 3 | `WDFC:14.132903146509339@244.08` |
| 2026-06-25 | 10:30 | 951 | 934 | 3 | 3 | `AVBP:103.00265750970438@33.49` |
| 2026-06-26 | 11:30 | 941 | 929 | 3 | 3 | `DASH:18.91931662370427@182.33` |

Result:

- all five recent completed sessions met the `900` decision-record floor at
  every sample set;
- all five sessions reached the deployed K=3 cap with `3` accepted records and
  `3` entry intents;
- the evidence supports the current proof posture as opportunity-rich enough to
  continue accumulating paper trades without widening `MAX_OPEN_POSITIONS`,
  reducing selectivity, or enabling additional strategies.

Decision: leave the production paper configuration unchanged. The previously
documented exact-universe and full-nightly replay audits remain positive-edge,
and this recent-session dry-run check shows that the current K=3 posture has
been consistently finding the maximum allowed entries in the latest completed
sessions. The remaining proof dependency is live paper execution from a
`2026-06-29`-or-later completed market session.

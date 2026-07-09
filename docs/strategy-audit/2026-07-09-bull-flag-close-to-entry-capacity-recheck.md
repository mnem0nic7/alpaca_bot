# Bull Flag Close-To-Entry And Capacity Recheck - 2026-07-09

## Context

The live paper proof is operationally healthy but still pending:

- readiness: `ready`
- proof: `pending`, `awaiting_min_trades`
- active strategy: `bull_flag`
- live posture: `MAX_OPEN_POSITIONS=1`,
  `ENTRY_MIN_CLOSE_TO_ENTRY_PCT=-0.01`
- remaining proof blockers: sample trades, active days, profit concentration,
  and strategy diversification
- current progress: 3 closed trades, `$14.99` P&L, 2 active days

The July 8 decision dry run showed that relaxing the lower close-to-entry guard
could increase same-day accepted candidates. This audit checks whether that
actually improves the live proof horizon when measured against the current
robustness gates instead of changing paper posture from a one-day anecdote.

Scenario equity note: the replay universe was nearly live-sized. Of 999
scenario files, 977 had `starting_equity=69006.57`; the remaining 22 carried
older nearby or default values.

## Commands

Both commands are read-only diagnostics. They compare baseline against
`ENTRY_MIN_CLOSE_TO_ENTRY_PCT=-1.0` using the live proof gates:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
alpaca-bot-backtest proof-horizon-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --max-open-positions 1 \
  --min-trades 30 \
  --min-pnl 0.01 \
  --min-active-days 5 \
  --min-profit-factor 1.20 \
  --max-single-win-pnl-share 0.50 \
  --max-eod-loss-share 0.50 \
  --lever-label 'W_min_close_to_entry:entry_min_close_to_entry_pct=-1.0' \
  --output /tmp/bull_flag_close_to_entry_k1.md \
  --json /tmp/bull_flag_close_to_entry_k1.json
```

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
alpaca-bot-backtest proof-horizon-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --max-open-positions 5 \
  --min-trades 30 \
  --min-pnl 0.01 \
  --min-active-days 5 \
  --min-profit-factor 1.20 \
  --max-single-win-pnl-share 0.50 \
  --max-eod-loss-share 0.50 \
  --lever-label 'W_min_close_to_entry:entry_min_close_to_entry_pct=-1.0' \
  --output /tmp/bull_flag_close_to_entry_k5.md \
  --json /tmp/bull_flag_close_to_entry_k5.json
```

## K=1 Result

| lever | starts passed | pass rate | first pass rate | trades | P&L | terminal blockers |
|---|---:|---:|---:|---:|---:|---|
| baseline | 233 | 84.42% | 31.64% | 331 | `$185.61` | active_days:6, positive_pnl:40, profit_concentration:3, profit_factor:41, sample_trades:20 |
| `ENTRY_MIN_CLOSE_TO_ENTRY_PCT=-1.0` | 234 | 84.78% | 24.21% | 336 | `$143.98` | active_days:7, positive_pnl:40, profit_concentration:2, profit_factor:40, sample_trades:24 |

At the live K=1 capacity, relaxing the lower close-to-entry guard adds only 5
replay trades, reduces total P&L by `$41.63`, and weakens first-threshold proof
passes. This does not justify a live paper posture change.

## K=5 Result

| lever | starts passed | pass rate | first pass rate | trades | P&L | terminal blockers |
|---|---:|---:|---:|---:|---:|---|
| baseline | 215 | 77.90% | 17.23% | 611 | `$298.55` | active_days:5, positive_pnl:35, profit_concentration:26, profit_factor:54, sample_trades:9 |
| `ENTRY_MIN_CLOSE_TO_ENTRY_PCT=-1.0` | 251 | 90.94% | 26.59% | 581 | `$300.61` | active_days:5, positive_pnl:18, profit_concentration:7, profit_factor:22, sample_trades:9 |

The combined K=5 plus relaxed-filter row improves eventual proof pass rate and
reduces terminal robustness blockers versus K=5 baseline. It still has a lower
first-threshold pass rate than the live K=1 baseline and requires increasing
paper exposure after the July 7 capacity-reduction audit found K=1 had the
strongest risk-adjusted edge.

## Decision

Do not change live paper posture on 2026-07-09.

- Do not relax `ENTRY_MIN_CLOSE_TO_ENTRY_PCT` at K=1. The full-universe proof
  horizon shows worse P&L and worse first-threshold robustness.
- Do not raise `MAX_OPEN_POSITIONS` from 1 based on proof horizon alone. K=5
  increases sample size, but capacity is a risk posture change and the prior
  capacity audit selected K=1 for stronger edge and lower clustered exposure.
- Treat K=5 plus `ENTRY_MIN_CLOSE_TO_ENTRY_PCT=-1.0` as a research candidate,
  not a promotion. It needs a fresh direct edge audit before it can supersede
  the July 7 K=1 capacity decision.

Current limiting factors remain live sample accumulation, profit concentration,
and the unapproved second-strategy gate. The correct near-term action is to let
the current K=1 paper proof continue collecting clean trades while using replay
to find a quality-preserving throughput lever.

## Direct Edge Prefilter

The K=5 plus relaxed-filter research candidate received a direct edge
prefilter after the proof-horizon comparison. This was intentionally a
deterministic sample check before spending a longer full-universe validation.

Command:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
alpaca-bot-backtest lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 240 \
  --sample-seed bull-flag-k5-relaxed-close-edge-prefilter-20260709 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 5 \
  --lever-label 'W_min_close_to_entry:entry_min_close_to_entry_pct=-1.0' \
  --top-k 2 \
  --output /tmp/bull_flag_k5_relaxed_close_edge_prefilter_240.md
```

Result:

| rank | lever | IS ci_low | delta ci_low | IS mean | IS trades | IS verdict | OOS ci_low | OOS verdict |
|---:|---|---:|---:|---:|---:|---|---:|---|
| 1 | baseline | -0.7339 | 0.0000 | 0.2952 | 95 | `no-evidence` | -1.7630 | `no-evidence` |
| 2 | `ENTRY_MIN_CLOSE_TO_ENTRY_PCT=-1.0` | -0.7511 | -0.0172 | 0.6596 | 89 | `no-evidence` | -1.9972 | `no-evidence` |

Conclusion: stop pursuing this combined K=5 relaxed-filter candidate for paper
promotion. It improved the proof-horizon table, but it did not improve direct
edge on the 240-scenario prefilter and had a worse OOS lower bound than
baseline. The next throughput fix needs to come from a different lever.

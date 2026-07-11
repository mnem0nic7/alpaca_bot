# Bull Flag Candidate Ranking Audit - 2026-07-11

## Context

The frozen paper posture has one available position and ranks simultaneous
fillable entry candidates by signal-bar close proximity to the entry level,
then relative volume. Prior work validated K=1 over wider capacity but had not
tested whether a different ordering of the scarce slot improved after-cost
edge.

This audit compares the current ordering with two research-only alternatives:

- `relative_volume`: relative volume first, then close proximity
- `balanced`: minimize the worse ordinal rank across close proximity and
  relative volume, then minimize the sum of both ranks

The implementation defaults to `close_to_entry`. Paper proof posture checks
also require that default, so adding the diagnostic cannot silently change the
frozen paper ordering.

## Method

- Scenario directory: `/var/lib/alpaca-bot/nightly/scenarios`
- Scenario universe: 976 symbols
- Scenario-universe SHA-256:
  `a5a928102a2ebfa117fe48bec31e74d378af8852e5c3a0e9160fe7de45fe956b`
- Fractionable universe: 974 symbols (`RR` and `SLS` excluded from fractional
  sizing)
- Fractionability SHA-256:
  `004176a0eb2045252cc3d660620e44c8e8a5f0dd2bf8728cb0e00ad071e8a41b`
- Strategy: `bull_flag`
- Scoring: cross-sectional portfolio replay, K=1
- Starting equity: `$69,004.06`
- Slippage: 2 bps per side
- Split: chronological 80/20 IS/OOS with point-in-time warmup
- Selection: all three preregistered modes received OOS evaluation

```bash
set -a
source /etc/alpaca_bot/alpaca-bot.env
set +a
PYTHONPATH=src python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --scenario-symbols-file /var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260711T043655Z/scenario_symbols.txt \
  --fractionable-symbols-file /var/lib/alpaca-bot/nightly/second_strategy/setup_knobs/20260711T043655Z/fractionable_symbols.txt \
  --strategy bull_flag \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 69004.06 \
  --lever-label AS_entry_candidate_rank:relative_volume \
  --lever-label AS_entry_candidate_rank:balanced \
  --top-k 3
```

The command completed all costed and frictionless IS/OOS replay legs. Its final
attempt to write under `/var/lib/alpaca-bot/nightly/diagnostics` failed with
`PermissionError`; the final per-leg metrics had already been emitted and are
recorded below.

## Results

| rank mode | IS trades | IS CI low | IS verdict | OOS trades | OOS CI low | OOS verdict |
|---|---:|---:|---|---:|---:|---|
| `close_to_entry` | 261 | -0.1923 | `no-evidence` | 83 | -0.5751 | `no-evidence` |
| `relative_volume` | 227 | -0.1958 | `no-evidence` | 74 | -0.9499 | `no-evidence` |
| `balanced` | 258 | -0.2423 | `no-evidence` | 84 | -0.7167 | `no-evidence` |

The baseline exactly reproduced the corrected July 11 no-follow-through sweep,
which is the control that the default-preserving engine refactor did not alter
existing replay behavior.

Relative-volume-first reduced IS trades by 13% and produced the worst OOS
lower bound. Balanced ranking preserved trade count but weakened both IS and
OOS lower bounds. Neither alternative reached a non-negative OOS confidence
interval floor.

## Decision

Keep `ENTRY_CANDIDATE_RANK_MODE=close_to_entry`. Do not change the paper
posture or restart proof accumulation. The cross-sectional ordering gap is now
closed for the existing proximity and relative-volume features; future ranking
research needs a genuinely new point-in-time signal-quality feature rather
than another ordering of these two inputs.

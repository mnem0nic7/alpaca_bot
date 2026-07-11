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

## Signal-Quality Follow-Up

A bounded follow-up tested one genuinely new point-in-time feature rather than
another permutation of close proximity and relative volume. For each accepted
`bull_flag` signal, the research branch computed the average normalized margin
above the strategy's three defining setup gates:

```text
run_quality = 1 - min_run_pct / observed_pole_run_pct
range_quality = 1 - observed_signal_range / allowed_signal_range
volume_quality = 1 - observed_signal_volume / allowed_signal_volume
signal_quality = mean(run_quality, range_quality, volume_quality)
```

The score was bounded to `[0,1]` and used only information available at the
signal timestamp. The preregistered candidate ranked by `signal_quality` first,
then retained close proximity, relative volume, and symbol as deterministic
tie-breakers. It used the same 976-symbol scenario universe, 974-symbol
fractionability snapshot, K=1, `$69,004.06` starting equity, 2 bps/side cost,
and chronological IS/OOS split documented above.

| rank mode | IS mean | IS trades | IS CI low | IS p | OOS trades | OOS CI low |
|---|---:|---:|---:|---:|---:|---:|
| `close_to_entry` | 0.6365 | 261 | -0.1923 | 0.0705 | 83 | -0.5751 |
| `signal_quality` | -0.1523 | 238 | -1.0944 | 0.6105 | 75 | -1.4294 |

The score selected fewer trades, turned mean IS P&L negative, weakened the IS
lower bound by `0.9021`, and also performed substantially worse OOS. The three
gate margins are therefore not monotonic trade-quality predictors in this
portfolio frame.

Decision: reject the score and remove its implementation rather than retain a
discredited production control. The active and committed code remains exactly
the deployed `close_to_entry` baseline. A future ranking feature must add
different information, not reweight these setup margins.

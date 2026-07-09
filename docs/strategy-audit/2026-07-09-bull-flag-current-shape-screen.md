# Bull Flag Current-Posture Shape Screen - 2026-07-09

## Context

The live paper proof uses `bull_flag`, `MAX_OPEN_POSITIONS=1`, the promoted
giveback exit, and 2 bps/side replay slippage. Earlier shape research used the
pre-giveback K=4 posture, while the significance-aware replay grid did not
expose the three defining bull-flag setup controls. This screen closes that
coverage gap and checks whether a modestly looser shape rule can increase proof
throughput without sacrificing edge.

The replay grid now includes one-factor-at-a-time families for:

- `BULL_FLAG_MIN_RUN_PCT`
- `BULL_FLAG_CONSOLIDATION_RANGE_PCT`
- `BULL_FLAG_CONSOLIDATION_VOLUME_RATIO`

The coarse grid uses the established looser values `0.015`, `0.6`, and `0.7`.
These are research candidates only; the defaults remain `0.02`, `0.5`, and
`0.6`.

## Discovery Screen

The deterministic 80-scenario screen used a chronological IS/OOS split, the
full cross-sectional K=1 portfolio path, `$69,004.08` starting equity, and the
current live paper settings.

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
PYTHONPATH=src python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 80 \
  --sample-seed bull-flag-current-shape-discovery-20260709-v1 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 69004.08 \
  --lever-label AP_bull_flag_min_run:bull_flag_min_run_pct=0.015 \
  --lever-label AQ_bull_flag_range:bull_flag_consolidation_range_pct=0.6 \
  --lever-label AR_bull_flag_volume:bull_flag_consolidation_volume_ratio=0.7 \
  --top-k 2 \
  --output /tmp/bull_flag_current_shape_discovery_80.md
```

Result:

| rule | IS trades | IS mean/trade | IS 95% CI low | p(mean<=0) | OOS trades | OOS 95% CI low | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 17 | -1.5589 | -4.6897 | 0.8505 | 7 | -4.9422 | `no-evidence` |
| minimum run `0.015` | 24 | -0.7410 | -3.1677 | 0.7135 | 7 | -4.9422 | `no-evidence` |
| range `0.6` | 17 | -1.5589 | -4.6897 | 0.8505 | not shortlisted | not run | `no-evidence` |
| volume ratio `0.7` | 21 | -1.1871 | -3.9516 | 0.8030 | 8 | -4.1731 | `no-evidence` |

## Decision

No candidate had a non-negative OOS confidence-interval floor, so none earned
a larger independent validation or proof-horizon run. Keep the live bull-flag
shape settings unchanged. The added grid coverage remains useful for future
audits and prevents these strategy-defining fields from being omitted from the
same evidence workflow used for other stock strategies.

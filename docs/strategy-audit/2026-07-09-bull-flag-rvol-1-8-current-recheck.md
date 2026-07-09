# Bull Flag RVOL 1.8 Current-Posture Recheck - 2026-07-09

## Context

The June 30 audit rejected `RELATIVE_VOLUME_THRESHOLD=1.8` under the then-live
K=4 posture. It increased throughput but weakened several robustness metrics.
The live posture later changed materially to `MAX_OPEN_POSITIONS=1` and
promoted the giveback exit, so the single previously promising throughput
value received a bounded current-posture recheck rather than a broader
threshold search.

The significance-aware replay grid now includes `1.8` explicitly. The recheck
used a deterministic 160-scenario sample with seed
`bull-flag-current-rvol-1-8-recheck-20260709-v1`, a chronological IS/OOS split,
the full cross-sectional K=1 portfolio path, 2 bps/side slippage, and
`$69,004.08` starting equity.

## Result

| posture | IS trades | IS mean/trade | IS 95% CI low | IS p(mean<=0) | OOS trades | OOS 95% CI low | OOS verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| threshold `2.0` | 46 | 3.9481 | 0.9211 | 0.0015 | 20 | -2.1327 | `no-evidence` |
| threshold `1.8` | 58 | 2.7333 | 0.1713 | 0.0135 | 22 | -2.0331 | `no-evidence` |

Threshold `1.8` again increased trade count, but its IS mean and confidence
floor were materially weaker than baseline, while neither posture retained a
non-negative OOS confidence-interval floor. No candidate survived OOS.

## Decision

Keep `RELATIVE_VOLUME_THRESHOLD=2.0`. Do not spend a larger independent
validation or alter paper settings from this result. The current proof must
accumulate without weakening the setup-quality threshold.

# Bull Flag Current Market-Context Screen - 2026-07-09

## Context

Regime, VIX, and sector filters were previously tested under the pre-giveback
K=4 paper posture. Sector gating had been the least-bad context row, but no
filter retained a non-negative OOS confidence-interval floor. The live posture
later changed materially to `MAX_OPEN_POSITIONS=1` with the giveback exit, so
the existing point-in-time context family received one bounded recheck.

The screen preregistered the complete existing family:

- `ENABLE_REGIME_FILTER=true`
- `ENABLE_VIX_FILTER=true`
- `ENABLE_SECTOR_FILTER=true`
- VIX and sector filters enabled together

## Screen

The deterministic 160-scenario sample used seed
`bull-flag-current-market-context-20260709-v1`, a chronological IS/OOS split,
the full cross-sectional K=1 portfolio path, 2 bps/side slippage, the current
live paper settings, and `$69,004.08` starting equity.

```bash
PYTHONPATH=src python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 160 \
  --sample-seed bull-flag-current-market-context-20260709-v1 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 69004.08 \
  --lever-label F_regime:on \
  --lever-label AC_vix:on \
  --lever-label AD_sector:on \
  --lever-label AE_vix_sector:vix=on,sector=on \
  --top-k 5 \
  --output /tmp/bull_flag_current_market_context_160.md
```

Result:

| context | IS trades | IS mean/trade | IS 95% CI low | p(mean<=0) | OOS trades | OOS 95% CI low | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline | 54 | 0.1133 | -1.6037 | 0.4360 | 34 | -0.6594 | `no-evidence` |
| regime | 40 | -0.2777 | -2.3441 | 0.6035 | 27 | -1.0101 | `no-evidence` |
| sector | 36 | -0.6134 | -2.6832 | 0.7255 | 31 | -1.1067 | `no-evidence` |
| VIX | 35 | -1.0288 | -3.2453 | 0.8190 | 34 | -0.6594 | `no-evidence` |
| VIX and sector | 27 | -1.1661 | -3.8604 | 0.8070 | 31 | -1.1067 | `no-evidence` |

## Decision

No context gate improved the IS confidence floor over baseline, and no row
retained a non-negative OOS floor. Do not enable regime, VIX, or sector filters
in paper and do not spend a larger independent validation on this family. The
current K=1 bull-flag posture remains unchanged.

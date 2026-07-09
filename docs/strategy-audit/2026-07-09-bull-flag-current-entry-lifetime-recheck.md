# Bull Flag Current Entry-Lifetime Recheck - 2026-07-09

## Context

The paper proof historically had a low entry fill rate, and an earlier K=4
screen showed that keeping stop-limit entries active for two or three bars
increased fills but failed OOS. The live posture later changed materially to
`MAX_OPEN_POSITIONS=1` with the giveback exit, so this final execution-quality
throughput family received one bounded current-posture recheck.

Only the existing off-default values were tested:

- `ENTRY_ORDER_ACTIVE_BARS=2`
- `ENTRY_ORDER_ACTIVE_BARS=3`

## Screen

The deterministic 160-scenario sample used seed
`bull-flag-current-entry-lifetime-20260709-v1`, a chronological IS/OOS split,
the full cross-sectional K=1 portfolio path, 2 bps/side slippage, the current
live paper settings, and `$69,004.08` starting equity.

```bash
PYTHONPATH=src python3 -m alpaca_bot.replay.cli lever-sweep \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --sample-size 160 \
  --sample-seed bull-flag-current-entry-lifetime-20260709-v1 \
  --slippage-bps 2 \
  --portfolio \
  --max-open-positions 1 \
  --starting-equity 69004.08 \
  --lever-label AG_entry_order_active_bars:2 \
  --lever-label AG_entry_order_active_bars:3 \
  --top-k 3 \
  --output /tmp/bull_flag_current_entry_lifetime_160.md
```

Result:

| active bars | IS trades | IS mean/trade | IS 95% CI low | p(mean<=0) | OOS trades | OOS 95% CI low | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1, baseline | 68 | -0.1248 | -1.2298 | 0.6040 | 31 | -2.9666 | `no-evidence` |
| 2 | 94 | -0.3696 | -1.4353 | 0.7640 | 33 | -2.1015 | `no-evidence` |
| 3 | 101 | -0.2639 | -1.1142 | 0.7370 | 33 | -1.3866 | `no-evidence` |

## Decision

Longer lifetimes materially increased IS fills, and three bars improved the CI
floor versus baseline, but every mean remained negative and no row retained a
non-negative OOS confidence-interval floor. Do not lengthen the paper entry
window and do not spend a larger independent validation on this family. Keep
`ENTRY_ORDER_ACTIVE_BARS=1`.

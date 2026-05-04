# Profitability Gate: Configurable OOS Score Floor and Gate Ratio

## Goal

Improve trade profitability by ensuring the nightly and evolve pipelines only accept parameter candidates with a demonstrably positive, quantifiable out-of-sample edge — not merely candidates that degrade less than 50% from in-sample to out-of-sample.

## Problem

The current walk-forward gate is:

```python
held_pairs = [
    (c, s) for c, s in zip(top10, oos_scores)
    if s is not None and c.score is not None and s >= c.score * 0.5
]
```

This is a **relative** gate only. A candidate with IS Sharpe = 0.04 and OOS Sharpe = 0.02 passes — both scores represent essentially no exploitable edge in live trading. The gate verifies consistency (OOS doesn't collapse entirely) but not profitability (OOS score is actually tradeable).

Two problems:

1. **No absolute floor**: candidates with negligible OOS scores are accepted and deployed.
2. **Gate ratio is hardcoded at 0.5**: operators cannot tighten it (e.g., to 0.7) to require better IS→OOS retention without changing code.

Additionally, `_print_walk_forward_block()` hardcodes `0.5` in its "held" calculation, creating a display inconsistency if the runtime gate is ever changed.

## Solution

Add two configurable parameters to both `alpaca-bot-nightly` and `alpaca-bot-evolve`:

### `--min-oos-score FLOAT` (default: `0.0`)

An absolute minimum OOS Sharpe/Calmar score. Candidates that pass the relative gate but fall below this floor are rejected. Default `0.0` preserves existing behaviour when not set.

**Example**: `--min-oos-score 0.3` means only candidates with OOS composite score ≥ 0.3 are accepted. Combined with the relative gate, this ensures the winner has genuine positive edge.

### `--oos-gate-ratio FLOAT` (default: `0.5`)

Replaces the hardcoded `0.5` in the relative gate. Configurable from 0.0 to 1.0. Higher values (e.g., 0.7) require better IS→OOS score retention, catching overfitted params earlier.

**Example**: `--oos-gate-ratio 0.7` means OOS score must be ≥ 70% of IS score to be held.

## Affected Code

### `src/alpaca_bot/nightly/cli.py`

- Add `--min-oos-score` and `--oos-gate-ratio` argparse arguments
- Update `held_pairs` filter to use both params
- Pass `oos_gate_ratio` and `min_oos_score` through to `_print_walk_forward_block`

### `src/alpaca_bot/tuning/cli.py`

- Add `--min-oos-score` and `--oos-gate-ratio` argparse arguments
- Update `held_pairs` filter
- Update `_print_walk_forward_block()` signature to accept `oos_gate_ratio` and `min_oos_score` keyword args
- Update `_print_walk_forward_block()` body: replace hardcoded `0.5` with the passed param; update the label to show the actual gate values; compute `held` consistently with the runtime gate

## What Does NOT Change

- `score_report()` — no change; the composite score formula is unchanged
- `evaluate_candidates_oos()` — no change
- `Settings` — these are pipeline configuration knobs, not live-trading knobs; they belong in CLI args only
- `split_scenario()` — no change
- The existing `--validate-pct` behaviour — no change
- The `--no-db` / `--dry-run` flags — no change
- Live trading engine (`evaluate_cycle()`) — not affected; this change is entirely in the nightly/tuning pipeline

## Safety

This is a conservative, read-only filter change. The only effect is that fewer candidates pass through to selection. No financial risk path is touched. No orders, positions, or stops are affected. The default values (`min_oos_score=0.0`, `oos_gate_ratio=0.5`) produce identical behaviour to the current code.

## Recommended Configuration

For production use, these values are recommended starting points:
- `--min-oos-score 0.2` — requires at least a modest positive Sharpe on OOS (rules out noise-level candidates)
- `--oos-gate-ratio 0.6` — requires OOS to retain 60% of IS performance (modest tightening)

These are operator choices, not defaults. Defaults stay at `0.0` / `0.5` for backward compatibility.

## Tests

- `tests/unit/test_nightly_cli.py`: add test `test_nightly_cli_min_oos_score_rejects_below_floor` — candidate passes relative gate but fails absolute floor; verify no held candidates, returns 0 (not 1)
- `tests/unit/test_tuning_sweep_cli.py`: add test `test_evolve_min_oos_score_rejects_below_floor` — same scenario for `alpaca-bot-evolve`
- No changes to existing tests; defaults preserve current behaviour

# 2026-07-08 Second Strategy Scale Grid Validation

## Context

The paper-proof blocker remains `strategy_diversification`: only `bull_flag`
is approved and active, while the proof gate requires two approved replay-backed
strategies.

This scan used the read-only second-strategy basket scanner after adding:

- candidate scale grid: `0.10,0.25,0.50`
- independent validation of positive prefilter survivors
- bounded parallel scan jobs: `2`
- default exclusion: `vwap_cross`

No live strategy, paper approval allowlist, or trading parameter was changed.

## Artifacts

```text
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/summary.json
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/validation/summary.md
/var/lib/alpaca-bot/nightly/second_strategy/20260708T015221Z/validation/summary.json
```

The scan updated:

```text
/var/lib/alpaca-bot/nightly/second_strategy/latest
/var/lib/alpaca-bot/nightly/second_strategy/latest_validation
```

## Prefilter Result

The 80-scenario prefilter found 19 positive-edge rows. Best per candidate:

| candidate | scale | trades | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `vwap_reversion` | 0.25 | 72 | 235.10 | [0.9670, 6.0543] | `positive-edge` |
| `ema_pullback` | 0.50 | 284 | 386.30 | [0.5703, 2.2382] | `positive-edge` |
| `gap_and_go` | 0.10 | 49 | 141.66 | [0.2999, 6.0981] | `positive-edge` |
| `high_watermark` | 0.10 | 49 | 141.66 | [0.2999, 6.0981] | `positive-edge` |
| `failed_breakdown` | 0.10 | 130 | 168.04 | [0.1551, 2.7017] | `positive-edge` |
| `bb_squeeze` | 0.10 | 175 | 162.43 | [0.1478, 1.9327] | `positive-edge` |
| `orb` | 0.10 | 452 | 269.17 | [0.0408, 1.1884] | `positive-edge` |

`breakout` and `momentum` did not produce positive-edge rows. `orb` was not
independently validated in this run because `SECOND_STRATEGY_MAX_VALIDATION_CANDIDATES=6`.

## Independent Validation

Validation used 160 scenarios, seed `second-strategy-independent-validation`,
2 bps/side slippage, K=1, and the same candidate scale selected by the prefilter.

| candidate | scale | trades | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `ema_pullback` | 0.50 | 351 | 168.60 | [-0.0807, 1.1487] | `no-evidence` |
| `failed_breakdown` | 0.10 | 191 | 112.77 | [-0.0909, 1.4134] | `no-evidence` |
| `bb_squeeze` | 0.10 | 286 | 13.28 | [-0.3402, 0.4296] | `no-evidence` |
| `high_watermark` | 0.10 | 68 | -2.41 | [-1.1999, 1.2399] | `no-evidence` |
| `gap_and_go` | 0.10 | 68 | -6.22 | [-1.3481, 1.2568] | `no-evidence` |
| `vwap_reversion` | 0.25 | 89 | -23.54 | [-2.0141, 1.6840] | `no-evidence` |

Conclusion: no candidate from this batch is approved for paper promotion.
Keep `PAPER_APPROVED_STRATEGIES` at `bull_flag` until a candidate survives
independent validation.

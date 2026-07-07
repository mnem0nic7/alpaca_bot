# Second strategy basket validation - 2026-07-07

Purpose: investigate the `strategy_diversification` paper-proof blocker after
operational readiness was restored. This audit did not enable any strategy and
did not change `PAPER_APPROVED_STRATEGIES`.

Live proof context before this audit:

- Mode: `paper`
- Strategy version: `v1-breakout`
- Readiness: `ready`
- Enabled approved replay strategy: `bull_flag`
- Diversification blocker: `active=1 required=2`
- Explicit guardrail: `vwap_cross` remains non-promotable.

## Basket prefilter

Command:

```bash
timeout 3600s scripts/second_strategy_basket_scan.sh /etc/alpaca_bot/alpaca-bot.env
```

Metadata:

- scenario_dir: `/var/lib/alpaca-bot/nightly/scenarios`
- base_strategy: `bull_flag`
- sample_size: `80`
- sample_seed: `second-strategy-k1-giveback-refresh`
- slippage_bps: `2.0`
- max_open_positions: `1`
- candidate_scale: `0.25`
- starting_equity: `68991.94`
- output_dir: `/tmp/alpaca-second-strategy-scan-20260707T231155Z`

Prefilter survivors:

| candidate | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | verdict |
|---|---:|---:|---:|---:|---|---:|---|
| `vwap_reversion` | 72 | 3.10 | 235.10 | 3.2653 | [0.9670, 6.0543] | 0.0015 | `positive-edge` |
| `ema_pullback` | 284 | 2.22 | 380.78 | 1.3408 | [0.5546, 2.2114] | 0.0005 | `positive-edge` |
| `gap_and_go` | 49 | 2.89 | 141.66 | 2.8910 | [0.2999, 6.0981] | 0.0105 | `positive-edge` |
| `high_watermark` | 49 | 2.89 | 141.66 | 2.8910 | [0.2999, 6.0981] | 0.0105 | `positive-edge` |
| `vwap_cross` | 265 | 1.82 | 253.34 | 0.9560 | [0.2333, 1.7763] | 0.0030 | `positive-edge` |
| `failed_breakdown` | 130 | 1.93 | 172.14 | 1.3241 | [0.1403, 2.7566] | 0.0095 | `positive-edge` |
| `bb_squeeze` | 178 | 1.77 | 175.23 | 0.9844 | [0.0965, 1.9474] | 0.0115 | `positive-edge` |

`orb`, `momentum`, and `breakout` were `no-evidence`. The prefilter result is
not approval; it only chooses candidates for an independent validation sample.

## Independent validation

Each validation used 160 scenarios, a different deterministic seed from the
prefilter, the same 2 bps/side replay cost, `max_open_positions=1`, current
broker equity as starting equity, and a 0.25 confidence scale for the candidate
strategy.

Commands followed this shape:

```bash
set -a
. /etc/alpaca_bot/alpaca-bot.env
set +a
export ENABLE_LIVE_TRADING=false TRADING_MODE=paper
python3 -m alpaca_bot.replay.cli portfolio-basket-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --strategy <candidate> \
  --sample-size 160 \
  --sample-seed <independent-seed> \
  --slippage-bps 2.0 \
  --max-open-positions 1 \
  --confidence-scale <candidate>=0.25 \
  --starting-equity 68991.94 \
  --output /tmp/<candidate>_basket_validation_160.md \
  --jsonl /tmp/<candidate>_basket_validation_160.jsonl
```

Results:

| candidate | validation seed | trades | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(mean<=0) | verdict |
|---|---|---:|---:|---:|---:|---|---:|---|
| `ema_pullback` | `second-strategy-ema-validation-20260707` | 385 | 1.21 | 108.31 | 0.2813 | [-0.2239, 0.8216] | 0.1335 | `no-evidence` |
| `vwap_reversion` | `second-strategy-vwap-reversion-validation-20260707` | 99 | 1.15 | 28.64 | 0.2893 | [-1.0832, 1.6940] | 0.3310 | `no-evidence` |
| `failed_breakdown` | `second-strategy-failed-breakdown-validation-20260707` | 205 | 1.07 | 24.33 | 0.1187 | [-0.7270, 1.0070] | 0.4105 | `no-evidence` |
| `bb_squeeze` | `second-strategy-bb-squeeze-validation-20260707` | 257 | 1.05 | 20.27 | 0.0789 | [-0.5978, 0.8409] | 0.4195 | `no-evidence` |
| `gap_and_go` | `second-strategy-gap-and-go-validation-20260707` | 79 | 1.89 | 108.47 | 1.3730 | [-0.2385, 3.3793] | 0.0580 | `no-evidence` |

`high_watermark` was not rerun independently because its prefilter row matched
`gap_and_go` exactly. `vwap_cross` was not considered for promotion despite a
positive prefilter row.

Conclusion: no disabled stock candidate earned replay approval as a second
paper strategy. The correct production action is to keep `PAPER_APPROVED_STRATEGIES`
at `bull_flag` and continue collecting live proof trades while the replay
tooling searches for a durable second strategy.

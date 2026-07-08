# 2026-07-08 Second Strategy Evidence

## Operational change

Commit `c564655` schedules `scripts/second_strategy_basket_scan.sh` after the
post-close nightly pipeline. The scan is read-only, writes persistent artifacts
under `/var/lib/alpaca-bot/nightly/second_strategy`, updates a `latest` symlink,
and excludes `vwap_cross` by default.

No live strategy, approval allowlist, or paper proof parameter was changed.

## Fresh prefilter

Command:

```bash
timeout 3600 ./scripts/second_strategy_basket_scan.sh /etc/alpaca_bot/alpaca-bot.env
```

Artifact:

```text
/var/lib/alpaca-bot/nightly/second_strategy/20260708T003439Z/summary.md
```

Positive prefilter rows:

| candidate | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `vwap_reversion` | 72 | 3.10 | 235.10 | [0.9670, 6.0543] | `positive-edge` |
| `ema_pullback` | 284 | 2.22 | 380.78 | [0.5546, 2.2114] | `positive-edge` |
| `gap_and_go` | 49 | 2.89 | 141.66 | [0.2999, 6.0981] | `positive-edge` |
| `high_watermark` | 49 | 2.89 | 141.66 | [0.2999, 6.0981] | `positive-edge` |
| `failed_breakdown` | 130 | 1.93 | 172.14 | [0.1403, 2.7566] | `positive-edge` |
| `bb_squeeze` | 178 | 1.77 | 175.23 | [0.0965, 1.9474] | `positive-edge` |

## Independent validation

Validation used a different seed, 160 scenarios, 2 bps slippage, K=1, and
candidate confidence scale 0.25.

Artifact:

```text
/var/lib/alpaca-bot/nightly/second_strategy/validation_20260708T005411Z/summary.md
```

| candidate | trades | profit factor | total P&L | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---|---|
| `vwap_reversion` | 114 | 1.06 | 11.47 | [-0.9598, 1.2693] | `no-evidence` |
| `ema_pullback` | 398 | 0.98 | -9.36 | [-0.4581, 0.4195] | `no-evidence` |
| `gap_and_go` | 90 | 1.18 | 22.70 | [-0.5706, 1.1588] | `no-evidence` |
| `high_watermark` | 90 | 1.19 | 23.55 | [-0.5642, 1.1682] | `no-evidence` |
| `failed_breakdown` | 186 | 1.01 | 2.68 | [-0.5653, 0.6320] | `no-evidence` |
| `bb_squeeze` | 284 | 1.17 | 61.56 | [-0.2429, 0.6810] | `no-evidence` |

Conclusion: no candidate from this batch is approved for paper promotion.

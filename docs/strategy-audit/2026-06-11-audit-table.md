> **Caveat (added post-diagnosis):** the ten zero-trade rows are replay-harness artifacts, not strategy verdicts — see §1.7 and §1.8 of `2026-06-11-contrarian-strategy-evaluation.md`. Only the momentum row reflects an actual measurement.

# Strategy audit — 5 bps/side vs frictionless

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | frictionless P&L | cost drag | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| breakout | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| momentum | 999 | 313 | 47.3% | 0.64 | -1123.50 | -3.5895 | -3.14 | [-6.2288, -0.9798] | 0.9950 | 445.40 | 1568.90 | **negative-edge** |
| orb | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| high_watermark | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| ema_pullback | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| vwap_reversion | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| gap_and_go | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| bull_flag | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| vwap_cross | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| bb_squeeze | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |
| failed_breakdown | 999 | 0 | n/a | n/a | 0.00 | n/a | n/a | n/a | n/a | 0.00 | 0.00 | **insufficient-data** |

# Bull Flag Recent Selectivity Audit - 2026-06-27

Purpose: tighten the paper proof posture using recent active-watchlist evidence
before the next paper session. This is a more recent-regime check than the
2026-06-26 999-symbol, 252-day K audit.

Data:

- 240 deterministic active paper watchlist symbols: `ORDER BY md5(symbol) LIMIT 240`
- 120 days of Alpaca daily and 15-minute bars
- Paper equity override: `$68,991.18`
- Replay slippage: `2` bps per side
- Strategy: `bull_flag`
- VWAP entry filter: enabled
- VIX, sector, regime, options, and extended-hours gates: disabled

Full-sample comparison:

| posture | trades | win rate | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(edge>0) | verdict |
|---|---:|---:|---:|---:|---:|---|---:|---|
| current: `RELATIVE_VOLUME_THRESHOLD=1.5`, `MAX_OPEN_POSITIONS=2` | 233 | 71.7% | 1.35 | 1164.23 | 4.9967 | [-1.1465, 11.1245] | 0.0525 | no-evidence |
| candidate: `RELATIVE_VOLUME_THRESHOLD=3.0`, `MAX_OPEN_POSITIONS=1` | 43 | 83.7% | 2.79 | 749.27 | 17.4249 | [2.1162, 31.2974] | 0.0135 | positive-edge |

Chronological 80/20 split:

| posture | split | trades | win rate | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(edge>0) | verdict |
|---|---|---:|---:|---:|---:|---:|---|---:|---|
| current: `RELATIVE_VOLUME_THRESHOLD=1.5`, `MAX_OPEN_POSITIONS=2` | IS | 163 | 73.0% | 1.74 | 1474.63 | 9.0468 | [1.3866, 16.3442] | 0.0105 | positive-edge |
| current: `RELATIVE_VOLUME_THRESHOLD=1.5`, `MAX_OPEN_POSITIONS=2` | OOS | 67 | 67.2% | 0.71 | -363.34 | -5.4229 | [-17.2577, 5.8143] | 0.8095 | no-evidence |
| candidate: `RELATIVE_VOLUME_THRESHOLD=3.0`, `MAX_OPEN_POSITIONS=1` | IS | 31 | 80.6% | 2.39 | 508.84 | 16.4143 | [-3.0968, 33.5473] | 0.0475 | no-evidence |
| candidate: `RELATIVE_VOLUME_THRESHOLD=3.0`, `MAX_OPEN_POSITIONS=1` | OOS | 12 | 91.7% | 5.50 | 238.37 | 19.8644 | [0.6821, 39.1871] | 0.0215 | positive-edge |

Decision:

Use `RELATIVE_VOLUME_THRESHOLD=3.0` and `MAX_OPEN_POSITIONS=1` for the current
paper proof. The stricter posture gives up turnover, but recent OOS evidence is
better: the current K=2 posture loses money OOS on this sample, while the
stricter K=1 posture remains positive-edge after 2 bps slippage. This change is
scoped to paper proof readiness and should be revisited after closed paper
trades accumulate.

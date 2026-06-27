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

Full nightly scenario validation:

After deployment, the stricter posture was also checked against the full
nightly 252-day scenario set using floor-sized paper equity. Live paper has no
closed `bull_flag` history yet, so the supervisor sizes entries at the
confidence floor: `$68,991.18 * 0.25 = $17,247.79`.

The scenario set was then refreshed from Alpaca. Four paper watchlist symbols
with no returned bars (`HEIA`, `MOGA`, `TBBQ`, `UHALB`) were marked ignored,
leaving `999` active paper symbols with `999` scenario files and `100.00%`
coverage.

Command:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 1 \
  --max-open-positions 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795
```

Full 252-day refreshed result:

| max open | scenarios | trades | win rate | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 999 | 326 | 73.6% | 1.68 | 891.50 | 2.7347 | [0.9796, 4.4462] | 0.0015 | positive-edge |
| 2 | 999 | 524 | 73.1% | 1.60 | 1314.00 | 2.5076 | [1.1327, 3.8851] | 0.0000 | positive-edge |
| 3 | 999 | 630 | 71.0% | 1.32 | 958.09 | 1.5208 | [0.2547, 2.8169] | 0.0070 | positive-edge |

All-symbol recent-window K recheck at relvol 3.0:

To make sure the K increase is not only a 252-day artifact, the refreshed
`999` scenario files were temporarily trimmed to their latest 120 daily bars
plus matching intraday bars and replayed with the same floor-sized equity.

| max open | scenarios | trades | win rate | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 1 | 999 | 124 | 79.8% | 2.01 | 431.08 | 3.4765 | [0.7756, 6.2071] | 0.0060 | positive-edge |
| 2 | 999 | 208 | 76.9% | 1.89 | 645.97 | 3.1056 | [1.1314, 5.0978] | 0.0005 | positive-edge |
| 3 | 999 | 255 | 74.1% | 1.41 | 466.20 | 1.8282 | [-0.2096, 3.8150] | 0.0360 | no-evidence |

Relvol 2.0 K proof-velocity recheck:

The current proof target needs closed paper trades. A follow-up check compared
`RELATIVE_VOLUME_THRESHOLD=2.0`, `MAX_OPEN_POSITIONS=2` with the relvol 3.0 K=2
posture across the same refreshed `999` active-symbol set. Relvol 2.5 K=2 was
also tested on the recent window and rejected because its confidence interval
fell below zero.

After relvol 2.0 K=2 was deployed, K=3 and K=4 were checked against both the
latest 120-day all-symbol window and the refreshed full 252-day window. K=3
improved proof velocity and total P&L versus K=2 while also improving the
confidence lower bound in both windows. K=4 added more trades but weakened
profit factor, mean/trade, Sharpe, and confidence lower bound versus K=3.

| posture | window | scenarios | trades | win rate | profit factor | total P&L | mean/trade | 95% CI mean/trade | p(edge>0) | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|
| 3.0 K=2 | 252d | 999 | 524 | 73.1% | 1.60 | 1314.00 | 2.5076 | [1.1327, 3.8851] | 0.0000 | positive-edge |
| 2.0 K=2 | 252d | 999 | 772 | 71.2% | 1.43 | 1336.29 | 1.7309 | [0.7219, 2.7372] | 0.0000 | positive-edge |
| 2.0 K=3 | 252d | 999 | 1056 | 71.8% | 1.43 | 1869.26 | 1.7701 | [0.9194, 2.7116] | 0.0005 | positive-edge |
| 2.0 K=4 | 252d | 999 | 1256 | 71.8% | 1.39 | 2082.76 | 1.6582 | [0.8334, 2.5009] | 0.0000 | positive-edge |
| 3.0 K=2 | latest 120d | 999 | 208 | 76.9% | 1.89 | 645.97 | 3.1056 | [1.1314, 5.0978] | 0.0005 | positive-edge |
| 2.5 K=2 | latest 120d | 999 | 257 | 73.9% | 1.40 | 418.18 | 1.6272 | [-0.1544, 3.4526] | 0.0340 | no-evidence |
| 2.0 K=2 | latest 120d | 999 | 322 | 75.8% | 1.77 | 849.10 | 2.6370 | [1.0794, 4.1225] | 0.0005 | positive-edge |
| 2.0 K=3 | latest 120d | 999 | 428 | 75.2% | 1.78 | 1114.85 | 2.6048 | [1.2761, 3.9764] | 0.0005 | positive-edge |
| 2.0 K=4 | latest 120d | 999 | 514 | 74.1% | 1.62 | 1171.17 | 2.2785 | [1.0344, 3.4679] | 0.0000 | positive-edge |

Decision:

Use `RELATIVE_VOLUME_THRESHOLD=2.0` and `MAX_OPEN_POSITIONS=3` for the current
paper proof. K=3 improves expected proof turnover versus K=2 while staying
positive-edge in both the refreshed full-252-day window and the all-symbol
recent 120-day window. K=4 is not promoted because the extra turnover weakens
the risk-adjusted evidence versus K=3. The old recent OOS losing K=2 row was
the prior `RELATIVE_VOLUME_THRESHOLD=1.5` posture, not this stricter relvol
posture. This change is scoped to paper proof readiness and should be revisited
after closed paper trades accumulate.

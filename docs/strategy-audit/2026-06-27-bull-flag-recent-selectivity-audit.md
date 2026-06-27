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

Live proof window:

The paper proof start is `2026-06-29`, the next regular market session after
this posture was verified and paper trading was confirmed enabled. The
`2026-06-26` session is excluded from the proof window because paper remained in
`close_only` from the earlier `2026-06-15` guard until after the June 26 market
close, producing 438/438 entries-disabled supervisor cycles and no valid
`bull_flag` decision-log rows for that session.
The proof probe evaluates only completed sessions by default; before the June
29 close it reports the proof as pending rather than scoring an in-progress
session.

Exact active-watchlist replay:

After the deployment, live paper had 982 enabled, non-ignored watchlist symbols.
The refreshed 999-scenario directory still covered every active symbol and had
17 inactive extras (`ACLX`, `APLS`, `BK`, `CSGS`, `CTLP`, `CVGW`, `EXPI`,
`FOLD`, `KALV`, `MASI`, `MCW`, `SEMR`, `SLNO`, `SNCY`, `STKL`, `UDMY`,
`VSCO`). The paper posture was rechecked against a temporary symlinked scenario
directory containing exactly those 982 active symbols.

Command:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /tmp/alpaca-active-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795
```

Result:

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 982 | 1050 | 71.6% | 1.42 | 1837.51 | 1.7500 | 3.66 | [0.8379, 2.6633] | 0.0000 | positive-edge |

Decision: keep the deployed `RELATIVE_VOLUME_THRESHOLD=2.0`,
`MAX_OPEN_POSITIONS=3`, floor-sized paper proof posture. Removing inactive
scenario extras does not weaken the evidence; the exact live active universe
still clears the positive-edge audit.

Exact active-watchlist recent-window replay:

The same 982 active symbols were also trimmed to their latest 120 daily bars
plus matching intraday bars to verify that the active-universe evidence still
holds in the recent regime.

Command:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795
```

Result:

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 982 | 424 | 74.8% | 1.75 | 1070.09 | 2.5238 | 5.82 | [1.1901, 3.8148] | 0.0000 | positive-edge |

Decision: the latest-120-day active-universe replay strengthens the same paper
proof posture. Keep `RELATIVE_VOLUME_THRESHOLD=2.0` and `MAX_OPEN_POSITIONS=3`
for the 2026-06-29 proof start.

Data freshness cleanup:

The active scenario set was checked for stale daily and intraday coverage after
the exact-active audit. Two enabled symbols were stale or too sparse for the
intraday bull-flag proof:

- `ALX`: refreshed daily bars reached `2026-06-26`, but intraday bars still
  stopped at `2026-06-25`; it was also the sparsest active intraday scenario
  (`394` bars across the 252-day lookback).
- `CWAN`: Alpaca returned no daily or intraday bars after `2026-06-24`.

Both symbols were marked ignored for paper entries with `WATCHLIST_IGNORE` audit
events. The live proof universe became 980 enabled, non-ignored symbols with 6
ignored symbols. Every active symbol still had a scenario file. The `ALX` and
`CWAN` scenario files were refreshed before rerunning the active-universe audit.

Exact 980-symbol full-window replay:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /tmp/alpaca-active-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 980 | 1050 | 71.6% | 1.42 | 1837.51 | 1.7500 | 3.66 | [0.8379, 2.6633] | 0.0000 | positive-edge |

Current-code confirmation: the exact 980-symbol full-window replay was rerun
after deploying commit `a06c465` on 2026-06-27 using a symlinked scenario
directory rebuilt directly from the live enabled, non-ignored watchlist. The
rerun completed successfully with the same 1050 trades, $1837.51 total P&L,
1.42 profit factor, and positive-edge verdict.

Exact 980-symbol latest-120-day replay:

```bash
set -a; source /etc/alpaca_bot/alpaca-bot.env; set +a
alpaca-bot-backtest portfolio-audit \
  --scenario-dir /tmp/alpaca-active-120d-scenarios \
  --strategy bull_flag \
  --slippage-bps 2 \
  --max-open-positions 3 \
  --starting-equity 17247.795
```

| scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge>0) | verdict |
|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| 980 | 424 | 74.8% | 1.75 | 1070.09 | 2.5238 | 5.82 | [1.1901, 3.8148] | 0.0000 | positive-edge |

Decision: keep the same deployed proof posture after the data-quality cleanup.
Removing stale/sparse active symbols did not weaken the full-window or recent
active-universe evidence.

Proof gate calibration:

The latest-120-day exact 980-symbol replay was also aggregated by close date to
check that the live paper proof gates match the deployed trade cadence. It
closed 424 trades over 96 trade days, averaging 3.53 trades per calendar day and
4.42 trades per trade day. Starting from the first trade date in that replay,
the cumulative proof probe's 10-trade threshold was reached on the third trade
day. Single-day trade counts were usually below 10; only one replay day reached
10 trades and one reached 12.

Decision: keep `PROFIT_PROBE_MIN_TRADES=10` as a cumulative proof threshold
from `2026-06-29`, and keep the daily `SESSION_GUARD_MIN_TRADES=10` as the
minimum sample before enforcing the same-day P&L gate. Requiring 10 trades for
every individual session would be noisier than the replay cadence supports.

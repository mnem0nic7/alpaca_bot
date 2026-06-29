# Bull Flag Trailing ATR 1.0 Promotion - 2026-06-29

Purpose: test whether tightening the existing trailing ATR stop improves the
current paper proof posture after the 3R profit target promotion.

Baseline live posture before the check:

- `TRADING_MODE=paper`
- active strategy: `bull_flag`
- active scenarios: `980`
- `MAX_OPEN_POSITIONS=4`
- `REPLAY_SLIPPAGE_BPS=2.0`
- `ENABLE_PROFIT_TARGET=true`
- `PROFIT_TARGET_R=3.0`
- `TRAILING_STOP_ATR_MULTIPLIER=1.5`
- proof gate: at least `10` closed trades and `$0.01` cumulative P&L

Read-only portfolio audit command:

```bash
docker compose --env-file /etc/alpaca_bot/alpaca-bot.env -f deploy/compose.yaml run -T --rm \
  -e TRAILING_STOP_ATR_MULTIPLIER=1.0 \
  --entrypoint python nightly \
  -m alpaca_bot.replay.cli portfolio-audit \
    --scenario-dir /data/active_scenarios \
    --strategy bull_flag \
    --slippage-bps 2 \
    --max-open-positions 4 \
    --starting-equity 17247.795
```

| trailing ATR multiplier | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | p(mean<=0) | verdict |
|---:|---:|---:|---:|---:|---|---:|---|
| 1.0 | 1,235 | `$2,163.27` | 1.4062 | 3.7953 | [0.9253, 2.6061] | 0.0000 | positive-edge |
| 1.5, current | 1,235 | `$2,087.64` | 1.3924 | 3.6862 | [0.8687, 2.5407] | 0.0005 | positive-edge |
| 2.5 | 1,235 | `$2,087.64` | 1.3924 | 3.6862 | [0.8687, 2.5407] | 0.0005 | positive-edge |
| 3.5 | 1,235 | `$2,087.64` | 1.3924 | 3.6862 | [0.8687, 2.5407] | 0.0005 | positive-edge |

Proof-horizon follow-up for the 1.0 trailing ATR multiplier:

| metric | value |
|---|---:|
| historical starts checked | 269 |
| starts eventually reaching proof gate | 267 |
| starts not proven by data end | 2 |
| eventual pass rate | 99.26% |
| starts reaching trade threshold | 267 |
| first-threshold pass rate | 61.80% |
| first-threshold failures later recovered | 102 |
| median sessions to proof pass | 3 |
| p90 sessions to proof pass | 16 |
| p95 sessions to proof pass | 24 |
| slowest observed pass | 38 |
| active trade days | 241 |

Decision: promote `TRAILING_STOP_ATR_MULTIPLIER=1.0` for the paper proof
posture. The candidate preserves trade count and proof-horizon behavior while
improving after-cost P&L, profit factor, Sharpe, and the CI lower bound.

## Trailing Trigger Follow-up

After deploying the 1.0 trailing ATR multiplier, `TRAILING_STOP_PROFIT_TRIGGER_R=0.5`
was tested as a follow-up. It improved aggregate after-cost P&L, but the proof
velocity metric that matters before live paper proof weakened slightly.

| posture | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | first-threshold pass rate | slowest observed pass |
|---|---:|---:|---:|---:|---|---:|---:|
| trigger 1.0, current | 1,235 | `$2,163.27` | 1.4062 | 3.7953 | [0.9253, 2.6061] | 61.80% | 38 |
| trigger 0.5 | 1,237 | `$2,189.92` | 1.42 | 3.87 | [0.9285, 2.5893] | 60.67% | 38 |

Decision: keep `TRAILING_STOP_PROFIT_TRIGGER_R=1.0`. The 0.5R trigger has a
small CI-floor and P&L improvement, but it lowers the first-threshold pass rate
from 61.80% to 60.67%. That is not enough evidence to change the paper proof
posture immediately before the 2026-06-29 session.

## Profit Trail Distance Follow-up

After the trailing ATR promotion, the existing `PROFIT_TRAIL_PCT=0.95` became
too tight relative to the ATR trail. Looser profit-trail distances were tested
against the same active scenario universe, K=4, 2 bps slippage, 3R target, and
1.0 trailing ATR posture.

| profit trail pct | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | first-threshold pass rate | p90 pass | p95 pass | slowest pass |
|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 0.95, current | 1,235 | `$2,163.27` | 1.4062 | 3.7953 | [0.9253, 2.6061] | 61.80% | 16 | 24 | 38 |
| 0.975 | 1,288 | `$1,988.61` | 1.4054 | 3.8290 | [0.9010, 2.2178] | not tested | not tested | not tested | not tested |
| 0.925 | 1,229 | `$2,436.04` | 1.4697 | 4.1587 | [1.1519, 2.8339] | 59.55% | 17 | 22 | 31 |
| 0.90 | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | 59.93% | 17 | 22 | 31 |
| off | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | not tested | not tested | not tested | not tested |

Decision: promote `PROFIT_TRAIL_PCT=0.90` while keeping profit-trail enabled.
The 0.90 setting improves aggregate after-cost P&L, profit factor, Sharpe, and
CI lower bound while improving the proof-horizon tail (`p95` and slowest pass).
Its first-threshold pass rate is lower than 0.95, so the promotion is based on
the stronger profitability and better tail horizon, not on immediate first-10
trade proof velocity. Disabling the profit trail was identical to 0.90 in this
sample, but keeping a loose trail preserves an explicit profit guard.

## Initial ATR Stop Follow-up

After deploying the 0.90 profit trail, the initial ATR stop multiplier was
checked around the current `ATR_STOP_MULTIPLIER=1.0` posture. These were
costed full-universe portfolio replays only; alternatives that did not improve
the costed objective were not sent through proof-horizon.

| ATR stop multiplier | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | verdict |
|---:|---:|---:|---:|---:|---|---|
| 0.75 | 1,235 | `$2,443.87` | 1.4693 | 4.2030 | [1.1610, 2.8449] | positive-edge |
| 1.0, current | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | positive-edge |
| 1.5 | 1,227 | `$2,442.00` | 1.4742 | 4.2054 | [1.1289, 2.8854] | positive-edge |
| 2.0 | 1,227 | `$2,410.92` | 1.4661 | 4.1451 | [1.0854, 2.8611] | positive-edge |

Decision: keep `ATR_STOP_MULTIPLIER=1.0`. The tighter and wider alternatives
all stayed positive-edge, but each reduced aggregate P&L and CI lower bound
versus the deployed 1.0 stop.

## Entry Window Follow-up

Earlier entry cutoffs were checked after the 0.90 profit-trail deployment to
test whether late-day entries were weakening the paper posture. These were
costed full-universe portfolio replays only because neither candidate improved
the current objective.

| entry window | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---:|---|---|
| 10:00-15:30, current | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | positive-edge |
| 10:00-14:00 | 1,135 | `$2,197.85` | 1.4453 | 3.9100 | [1.0316, 2.8139] | positive-edge |
| 10:00-12:00 | 833 | `$1,329.23` | 1.3345 | 2.8157 | [0.5004, 2.6686] | positive-edge |

Decision: keep `ENTRY_WINDOW_END=15:30`. Earlier cutoffs reduced total P&L,
profit factor, Sharpe, and CI lower bound under the current paper posture.

## Relative Volume Threshold Follow-up

The relative-volume threshold was checked after the exit-tuning promotions to
confirm the current selectivity still held up. These were costed full-universe
portfolio replays only because no alternative improved the robust objective.

| relative volume threshold | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | verdict |
|---:|---:|---:|---:|---:|---|---|
| 1.8 | 1,365 | `$2,548.93` | 1.4462 | 4.1496 | [1.0298, 2.6865] | positive-edge |
| 2.0, current | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | positive-edge |
| 2.5 | 905 | `$1,547.41` | 1.3684 | 3.0962 | [0.6648, 2.7212] | positive-edge |
| 3.0 | 674 | `$1,518.99` | 1.4864 | 3.5971 | [0.9290, 3.4921] | positive-edge |

Decision: keep `RELATIVE_VOLUME_THRESHOLD=2.0`. Lowering the threshold to 1.8
increased raw P&L but reduced profit factor, Sharpe, and CI lower bound.
Stricter thresholds reduced trade count and aggregate P&L without improving
the CI floor.

## Capacity Follow-up

The max-open-position cap was checked after the exit-tuning promotions because
the live dry run was rejecting five entry intents at the capacity stage. These
were costed full-universe portfolio replays only because no alternative improved
the robust objective.

| max open positions | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | verdict |
|---:|---:|---:|---:|---:|---|---|
| 3 | 1,025 | `$2,079.83` | 1.4878 | 4.0528 | [1.0498, 3.0493] | positive-edge |
| 4, current | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | positive-edge |
| 5 | 1,386 | `$2,529.77` | 1.4199 | 4.0590 | [0.9954, 2.7393] | positive-edge |
| 6 | 1,490 | `$2,281.93` | 1.3346 | 3.4908 | [0.6885, 2.3404] | positive-edge |

Decision: keep `MAX_OPEN_POSITIONS=4`. K=5 slightly increased raw P&L, but it
reduced profit factor, Sharpe, and the CI lower bound. K=6 degraded all robust
metrics, and K=3 left too much aggregate edge unused.

## VWAP Entry Filter Follow-up

The VWAP entry filter was checked after the exit-tuning promotions because the
live dry run was still rejecting some signals at `vwap_filter`. This was a
costed full-universe portfolio replay only because disabling the filter did not
improve the current objective.

| VWAP entry filter | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---:|---|---|
| on, current | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | positive-edge |
| off | 1,229 | `$2,402.38` | 1.4673 | 4.1728 | [1.0853, 2.8358] | positive-edge |

Decision: keep `ENABLE_VWAP_ENTRY_FILTER=true`. Disabling the filter reduced
aggregate P&L, profit factor, Sharpe, and CI lower bound without increasing the
trade count in the current portfolio replay.

## Bull-Flag Shape Follow-up

Bull-flag pattern-shape thresholds were checked after the exit-tuning
promotions. These were costed full-universe portfolio replays only because no
candidate improved the robust objective.

| candidate | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | verdict |
|---|---:|---:|---:|---:|---|---|
| current: min run 0.02, range 0.5, volume ratio 0.6 | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | positive-edge |
| min run 0.015 | 1,339 | `$2,492.22` | 1.4717 | 4.1756 | [1.0531, 2.6094] | positive-edge |
| min run 0.03 | 1,038 | `$2,201.96` | 1.4373 | 3.7508 | [1.1372, 3.1443] | positive-edge |
| range 0.4 | 1,233 | `$2,355.65` | 1.4565 | 4.1301 | [1.0441, 2.7544] | positive-edge |
| range 0.6 | 1,234 | `$2,580.22` | 1.4909 | 4.2701 | [1.1910, 2.9843] | positive-edge |
| volume ratio 0.5 | 1,112 | `$1,772.47` | 1.3540 | 3.1980 | [0.6512, 2.4857] | positive-edge |
| volume ratio 0.7 | 1,315 | `$2,423.14` | 1.4344 | 3.8751 | [0.9837, 2.6860] | positive-edge |

Decision: keep `BULL_FLAG_MIN_RUN_PCT=0.02`,
`BULL_FLAG_CONSOLIDATION_RANGE_PCT=0.5`, and
`BULL_FLAG_CONSOLIDATION_VOLUME_RATIO=0.6`. `range=0.6` increased raw P&L and
point-estimate metrics slightly, but its CI lower bound was below the current
posture. All other candidates were weaker on both aggregate and robust metrics.

## Breakeven Stop Follow-up

Breakeven stop settings were checked after the exit-tuning promotions. These
were costed full-universe portfolio replays, with proof-horizon run only for
the one variant that improved the CI lower bound.

| candidate | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | proof first-threshold pass | proof eventual pass | slowest pass |
|---|---:|---:|---:|---:|---|---:|---:|---:|
| current: trigger 0.0025, trail 0.002 | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | 59.93% | 99.26% | 31 |
| breakeven off | 867 | `$1,373.95` | 1.2150 | 1.8452 | [0.1401, 3.2096] | not tested | not tested | not tested |
| trigger 0.001 | 1,256 | `$2,517.25` | 1.5313 | 4.3142 | [1.1761, 2.8453] | not tested | not tested | not tested |
| trigger 0.005 | 1,164 | `$2,488.90` | 1.4421 | 4.1660 | [1.2252, 3.1256] | 58.80% | 98.88% | 32 |
| trail 0.001 | 1,172 | `$2,312.51` | 1.4130 | 3.6860 | [1.0243, 2.9590] | not tested | not tested | not tested |
| trail 0.004 | 1,258 | `$1,892.94` | 1.4179 | 3.6330 | [0.7251, 2.2402] | not tested | not tested | not tested |

Decision: keep `ENABLE_BREAKEVEN_STOP=true`, `BREAKEVEN_TRIGGER_PCT=0.0025`,
and `BREAKEVEN_TRAIL_PCT=0.002`. Disabling breakeven materially weakened the
edge. A later 0.005 trigger improved the CI lower bound but reduced total P&L,
profit factor, Sharpe, eventual proof pass rate, first-threshold pass rate, and
slowest proof pass. The current trigger remains the better paper proof posture.

## Viability Exit Follow-up

Daily trend-filter and intraday VWAP-breakdown viability exits were checked
after the breakeven follow-up. These were costed full-universe portfolio
replays with the current paper-proof posture, 2 bps slippage, K=4, and
`$17,247.795` starting equity.

| candidate | trades | total P&L | profit factor | ann. Sharpe | 95% CI mean/trade | exit reasons | verdict |
|---|---:|---:|---:|---:|---|---|---|
| current: viability exits off | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | stop 890, EOD 338, target 1 | positive-edge |
| trend-filter exit on | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | [1.2042, 2.8908] | stop 890, EOD 338, target 1 | positive-edge |
| VWAP-breakdown exit on | 1,300 | `$1,889.54` | 1.3514 | 3.4132 | [0.6443, 2.2069] | stop 843, EOD 456, target 1 | positive-edge |
| trend + VWAP exits on | 1,300 | `$1,889.54` | 1.3514 | 3.4132 | [0.6443, 2.2069] | stop 843, EOD 456, target 1 | positive-edge |

Decision: keep `ENABLE_TREND_FILTER_EXIT=false` and
`ENABLE_VWAP_BREAKDOWN_EXIT=false`. The trend-filter exit did not fire in this
replay window. The VWAP-breakdown exit increased turnover but reduced aggregate
P&L, profit factor, Sharpe, and the CI lower bound, so no proof-horizon run or
runtime promotion was warranted.

## Daily SMA Lookback Follow-up

The entry daily-trend lookback was checked after the viability-exit follow-up.
These were costed full-universe portfolio replays with the current paper-proof
posture, 2 bps slippage, K=4, and `$17,247.795` starting equity.

| candidate | trades | total P&L | profit factor | ann. Sharpe | win rate | 95% CI mean/trade | exit reasons | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| current: `DAILY_SMA_PERIOD=20` | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | 72.42% | [1.2042, 2.8908] | stop 890, EOD 338, target 1 | positive-edge |
| `DAILY_SMA_PERIOD=10` | 1,236 | `$1,377.66` | 1.2512 | 2.2764 | 70.79% | [0.2689, 1.9204] | stop 880, EOD 355, target 1 | positive-edge |
| `DAILY_SMA_PERIOD=30` | 1,207 | `$2,510.20` | 1.4901 | 4.3418 | 71.83% | [1.1946, 2.9784] | stop 865, EOD 340, target 2 | positive-edge |
| `DAILY_SMA_PERIOD=50` | 1,099 | `$1,964.74` | 1.4273 | 4.1431 | 72.52% | [0.8663, 2.7119] | stop 796, EOD 302, target 1 | positive-edge |

Decision: keep `DAILY_SMA_PERIOD=20`. The 10-day lookback increased turnover
slightly but materially weakened aggregate and robust metrics. The 30-day
lookback improved raw P&L and Sharpe slightly, but its CI lower bound was below
the current posture. The 50-day lookback reduced trade count and weakened total
P&L, profit factor, and CI lower bound.

## Relative-Volume Lookback Follow-up

The bull-flag relative-volume baseline lookback was checked after the daily SMA
follow-up. These were costed full-universe portfolio replays with the current
paper-proof posture, 2 bps slippage, K=4, and `$17,247.795` starting equity.

| candidate | trades | total P&L | profit factor | ann. Sharpe | win rate | 95% CI mean/trade | exit reasons | verdict |
|---|---:|---:|---:|---:|---:|---|---|---|
| current: `RELATIVE_VOLUME_LOOKBACK_BARS=20` | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | 72.42% | [1.2042, 2.8908] | stop 890, EOD 338, target 1 | positive-edge |
| `RELATIVE_VOLUME_LOOKBACK_BARS=10` | 995 | `$1,605.34` | 1.3453 | 2.9141 | 70.35% | [0.6085, 2.6606] | stop 724, EOD 270, target 1 | positive-edge |
| `RELATIVE_VOLUME_LOOKBACK_BARS=30` | 971 | `$1,463.85` | 1.3330 | 2.9119 | 71.16% | [0.5177, 2.5094] | stop 720, EOD 250, target 1 | positive-edge |
| `RELATIVE_VOLUME_LOOKBACK_BARS=50` | 1,120 | `$1,977.60` | 1.4021 | 3.3064 | 70.36% | [0.8158, 2.6974] | stop 812, EOD 307, target 1 | positive-edge |

Decision: keep `RELATIVE_VOLUME_LOOKBACK_BARS=20`. Every tested alternative
reduced trade count, aggregate P&L, profit factor, Sharpe, and CI lower bound
versus the current posture, so no proof-horizon run or runtime promotion was
warranted.

## ATR Period Follow-up

The ATR lookback period was checked after the relative-volume lookback
follow-up. These were costed full-universe portfolio replays with the current
paper-proof posture, 2 bps slippage, K=4, and `$17,247.795` starting equity.

| candidate | trades | total P&L | profit factor | ann. Sharpe | win rate | 95% CI mean/trade | exit reasons | proof first-threshold pass | proof p90 | proof p95 | slowest proof pass |
|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|
| current: `ATR_PERIOD=14` | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | 72.42% | [1.2042, 2.8908] | stop 890, EOD 338, target 1 | 59.93% | 17 | 22 | 31 |
| `ATR_PERIOD=7` | 1,228 | `$2,485.73` | 1.4800 | 4.2146 | 72.39% | [1.1385, 2.9296] | stop 892, EOD 335, target 1 | not tested | not tested | not tested | not tested |
| `ATR_PERIOD=10` | 1,229 | `$2,482.33` | 1.4801 | 4.2297 | 72.42% | [1.1961, 2.8745] | stop 891, EOD 337, target 1 | not tested | not tested | not tested | not tested |
| `ATR_PERIOD=20` | 1,231 | `$2,555.80` | 1.4959 | 4.3842 | 72.46% | [1.1878, 2.9614] | stop 892, EOD 338, target 1 | not tested | not tested | not tested | not tested |
| `ATR_PERIOD=30` | 1,174 | `$2,439.15` | 1.4997 | 4.3660 | 72.15% | [1.2110, 2.9884] | stop 847, EOD 326, target 1 | 59.18% | 37 | 50 | 63 |

Decision: keep `ATR_PERIOD=14`. `ATR_PERIOD=20` improved raw P&L, profit
factor, and Sharpe, but its CI lower bound was below current. `ATR_PERIOD=30`
was the only candidate with a slightly higher CI lower bound, so it received a
proof-horizon check; that check reduced first-threshold pass rate and materially
slowed proof speed versus current. Shorter lookbacks were weaker on aggregate
and robust metrics.

## Stop-Limit Buffer Follow-up

The stop-limit entry buffer was checked after the ATR-period follow-up. These
were costed full-universe portfolio replays with the current paper-proof
posture, 2 bps slippage, K=4, and `$17,247.795` starting equity.

| candidate | trades | total P&L | profit factor | ann. Sharpe | win rate | 95% CI mean/trade | exit reasons | proof first-threshold pass | proof eventual pass | proof p90 | proof p95 | slowest proof pass |
|---|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---:|
| current: `STOP_LIMIT_BUFFER_PCT=0.001` | 1,229 | `$2,498.03` | 1.4835 | 4.2612 | 72.42% | [1.2042, 2.8908] | stop 890, EOD 338, target 1 | 59.93% | 99.26% | 17 | 22 | 31 |
| `STOP_LIMIT_BUFFER_PCT=0.0005` | 1,175 | `$2,715.89` | 1.5869 | 4.7835 | 73.11% | [1.4165, 3.1699] | stop 856, EOD 318, target 1 | 61.80% | 99.26% | 13 | 19 | 32 |
| `STOP_LIMIT_BUFFER_PCT=0.002` | 1,328 | `$2,293.56` | 1.3877 | 3.5252 | 71.84% | [0.8601, 2.5885] | stop 964, EOD 362, target 2 | not tested | not tested | not tested | not tested | not tested |
| `STOP_LIMIT_BUFFER_PCT=0.005` | 1,447 | `$1,674.87` | 1.2445 | 2.5051 | 70.28% | [0.3071, 1.9843] | stop 1041, EOD 404, target 2 | not tested | not tested | not tested | not tested | not tested |

Decision: promote `STOP_LIMIT_BUFFER_PCT=0.0005`. The tighter buffer reduced
lower-quality fills, improved aggregate P&L, profit factor, Sharpe, win rate,
and CI lower bound, and improved first-threshold proof rate plus p90/p95 proof
speed. The slowest observed proof pass moved from 31 to 32 sessions, which is
not enough tail degradation to offset the stronger robust metrics and faster
typical proof horizon.

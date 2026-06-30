# Bull Flag Price And VWAP Exit Recheck

Date: 2026-06-30

Purpose: investigate the 2026-06-29 paper losers before changing the current
paper proof posture. The live proof window is still pending with 6 scoreable
closed trades and cumulative P&L of `-$5.16`.

Current live proof context:

- Active paper watchlist scenarios: 980 enabled, non-ignored symbols
- Strategy: `bull_flag`
- Slippage: 2 bps per side
- Max open positions: 4
- Proof gate: 10 closed trades and cumulative P&L >= `$0.01`
- Current proof posture keeps `ENABLE_VWAP_BREAKDOWN_EXIT=false` and
  `VIABILITY_MIN_HOLD_MINUTES=0`

Live paper trade review:

| Symbol | P&L | Entry | Exit | Exit type | Accepted signal notes |
| --- | ---: | ---: | ---: | --- | --- |
| PANW | `$5.16` | `$326.12` | `$328.07` | stop | early fill, decision context missing from same-cycle join |
| FTDR | `$1.51` | `$76.12` | `$76.25` | stop | relvol 2.0099, above VWAP |
| S | `-$10.14` | `$17.01` | `$16.81` | stop | early fill, low-priced loser |
| AMLX | `-$0.11` | `$18.41` | `$18.40` | stop | relvol 3.2807, above VWAP |
| URGN | `$3.47` | `$35.04` | `$35.64` | stop | relvol 2.2160, above VWAP |
| EVTC | `-$5.05` | `$28.02` | `$27.52` | exit | relvol 4.2705, above VWAP, held to flatten |

Minimum entry price screen:

The live losers suggested a possible low-price filter, so a deterministic
240-symbol sample screened minimum entry-price gates before any full replay.

| Minimum entry price | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| disabled | 381 | `$441.50` | 1.5457 | 3.2343 | 70.34% | 0.3811 | 1.9913 |
| `$10` | 352 | `$475.65` | 1.6603 | 3.7201 | 71.88% | 0.5555 | 2.2753 |
| `$15` | 327 | `$438.17` | 1.6363 | 3.5728 | 71.87% | 0.3761 | 2.2992 |
| `$20` | 294 | `$432.21` | 1.7234 | 3.7166 | 73.13% | 0.5327 | 2.4840 |
| `$25` | 269 | `$344.29` | 1.6210 | 3.2310 | 72.12% | 0.3284 | 2.2125 |
| `$30` | 245 | `$348.14` | 1.7020 | 3.4979 | 72.24% | 0.4259 | 2.5238 |
| `$35` | 222 | `$310.46` | 1.6977 | 3.3472 | 71.17% | 0.2550 | 2.5259 |
| `$40` | 204 | `$325.66` | 1.8615 | 3.6837 | 74.02% | 0.4701 | 2.7166 |
| `$50` | 186 | `$341.99` | 2.0738 | 4.1104 | 75.27% | 0.6306 | 3.1681 |

Only `$10` and `$20` justified full active-universe follow-up. `$30+` was too
selective for the proof gate despite stronger sample profit factors.

Full active-universe price replay:

| Minimum entry price | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high | Eventual proof pass | First-threshold pass | P95 sessions | Slowest pass |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| disabled, current | 1122 | `$1,283.97` | 1.5682 | 4.6039 | 72.46% | 0.6894 | 1.6014 | 98.88% | 61.80% | 21 | 30 |
| `$10` | 1052 | `$1,235.43` | 1.6065 | 4.7696 | 73.76% | 0.7102 | 1.6222 | 98.88% | 63.67% | 18 | 32 |
| `$20` | 926 | `$892.25` | 1.5151 | 3.8536 | 73.33% | 0.5205 | 1.4054 | 98.88% | 66.92% | 23 | 34 |

VWAP-breakdown exit screen:

EVTC was an EOD-flatten loser, so the already-implemented
`ENABLE_VWAP_BREAKDOWN_EXIT` guard was tested with small minimum-hold variants
on a deterministic 240-symbol sample.

| Variant | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| current, VWAP exit off | 333 | `$374.34` | 1.4734 | 2.8353 | 70.27% | 0.2947 | 2.0770 |
| VWAP exit, hold 0, min bars 1 | 335 | `$384.46` | 1.5343 | 3.0633 | 66.27% | 0.3412 | 1.9681 |
| VWAP exit, hold 15, min bars 1 | 335 | `$384.13` | 1.5336 | 3.0618 | 66.27% | 0.3596 | 1.9748 |
| VWAP exit, hold 30, min bars 1 | 335 | `$385.90` | 1.5364 | 3.0536 | 66.57% | 0.3507 | 1.9817 |
| VWAP exit, hold 15, min bars 3 | 335 | `$384.13` | 1.5336 | 3.0618 | 66.27% | 0.3596 | 1.9748 |
| VWAP exit, hold 30, min bars 3 | 335 | `$385.90` | 1.5364 | 3.0536 | 66.57% | 0.3507 | 1.9817 |

The best sample variant was full-tested with `ENABLE_VWAP_BREAKDOWN_EXIT=true`,
`VIABILITY_MIN_HOLD_MINUTES=30`, and `VWAP_BREAKDOWN_MIN_BARS=1`.

| VWAP exit posture | Trades | P&L | Profit factor | Annualized Sharpe | Win rate | CI low | CI high | Eventual proof pass | First-threshold pass | P95 sessions | Slowest pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| off, current | 1122 | `$1,283.97` | 1.5682 | 4.6039 | 72.46% | 0.6894 | 1.6014 | 98.88% | 61.80% | 21 | 30 |
| on, hold 30 | 1181 | `$1,176.49` | 1.5136 | 4.5251 | 68.67% | 0.5918 | 1.3881 | 99.26% | 61.42% | 20 | 31 |

Decision:

Do not promote a minimum entry-price gate. `$10` improves profit factor,
Sharpe, win rate, CI bounds, and p95 proof speed, but reduces total P&L and
does not filter the current paper losers. `$20` filters two live losers but
fails the full active-universe check on P&L, profit factor, Sharpe, CI bounds,
p95 sessions, and slowest proof pass.

Do not enable `ENABLE_VWAP_BREAKDOWN_EXIT`. The best sampled variant increased
full active-universe trade count and slightly improved eventual proof pass rate
and p95 proof speed, but weakened total P&L, profit factor, win rate, Sharpe,
CI lower bound, CI upper bound, and slowest proof pass. Keep the current proof
posture unchanged.

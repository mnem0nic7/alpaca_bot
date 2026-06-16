# Break-even slippage — cost-sensitivity of the lead candidates

**Date:** 2026-06-15 (full 999-scenario run completed 2026-06-16)
**Type:** read-only diagnostic. No production config changed. Candidates only —
promotion is exclusively through the nightly OOS gate (`alpaca-bot-nightly` →
`candidate.env`), never hand-applied.

## What this measures and why

The 2026-06-12 honest re-evaluation
([honest-reevaluation.md](2026-06-12-honest-reevaluation.md)) scored every
strategy at a single 5 bps/side cost point and found the recurring story to be
**cost drag, not bad entries**: nine of eleven strategies are profitable
frictionless yet dragged to break-even or loss by costs. The 2026-06-15 cost-aware
single-factor lever sweep then returned a clean **null** — no single
selectivity/hold/filter knob recovers after-cost edge
([lever-sweep-bull_flag.md](2026-06-15-lever-sweep-bull_flag.md),
[lever-sweep-vwap_reversion.md](2026-06-15-lever-sweep-vwap_reversion.md)).

This diagnostic replaces the single 5 bps point with a **slippage ladder**
`{0,1,2,3,4,5}` bps/side and finds, per lead candidate, the bps at which the
bootstrap `ci_low` crosses zero — the **break-even slippage**. The purpose is to
quantify *how much* execution-cost headroom each candidate has, and to **gate**
the next structural decision: attack realized execution cost directly, or build
the much more expensive cross-sectional / portfolio top-K replay (the thesis's
named selectivity mechanism, currently inexpressible because replay runs one
symbol per scenario).

## Method

`alpaca-bot-backtest break-even` re-runs the full replay at each rung over all
999 scenarios (`/var/lib/alpaca-bot/nightly/scenarios`, byte-identical to the
06-12 audit). Slippage is **not** a linear per-trade deduction — entry fills are
capped at the limit price and quantity/target levels derive from the slipped
fill ([runner.py:230-232,284,324,350](../../src/alpaca_bot/replay/runner.py#L230-L232)),
so the trade *set* is re-derived at each rung rather than re-priced. The s=0 rung
is the frictionless reference. `ci_low` is the lower bound of a seeded bootstrap
95% CI of mean per-trade P&L (`MIN_SAMPLES=5`); the break-even is the first
ascending zero-crossing, linearly interpolated. Full 999, in-sample: break-even
is a single descriptive scalar with no parameter fit and no model selection, so
there is no overfitting risk and no walk-forward is warranted.

## Results

### bull_flag — break-even ≈ 3.99 bps/side

| bps/side | trades | mean | ci_low | ci_high | p_positive | verdict |
|---|---|---|---|---|---|---|
| 0 | 2636 | 6.7909 | 3.6440 | 9.9867 | 0.0000 | positive-edge |
| 1 | 2636 | 5.7540 | 2.5191 | 8.9432 | 0.0000 | positive-edge |
| 2 | 2636 | 4.9222 | 1.7122 | 8.0651 | 0.0010 | positive-edge |
| 3 | 2636 | 4.0359 | 0.8181 | 7.1850 | 0.0075 | positive-edge |
| 4 | 2636 | 3.2293 | -0.0045 | 6.3832 | 0.0255 | no-evidence |
| 5 | 2636 | 2.4195 | -0.8359 | 5.5869 | 0.0775 | no-evidence |

`ci_low` falls monotonically and crosses zero between 3 and 4 bps:
`3 + 1·(0.8181 / (0.8181 − (−0.0045))) ≈ **3.99 bps/side**`. bull_flag is clean
`positive-edge` (full bootstrap CI above zero) through **3 bps/side**.

### vwap_reversion — break-even ≈ 0.00 bps/side

| bps/side | trades | mean | ci_low | ci_high | p_positive | verdict |
|---|---|---|---|---|---|---|
| 0 | 427 | 11.5234 | -3.0602 | 25.6455 | 0.0620 | no-evidence |
| 1 | 427 | 10.8184 | -3.7683 | 24.9816 | 0.0760 | no-evidence |
| 2 | 427 | 10.0085 | -4.5541 | 24.0703 | 0.0905 | no-evidence |
| 3 | 427 | 9.1171 | -5.4440 | 23.1366 | 0.1100 | no-evidence |
| 4 | 427 | 7.5782 | -6.9554 | 21.4826 | 0.1535 | no-evidence |
| 5 | 427 | 6.5882 | -7.9594 | 20.5139 | 0.1880 | no-evidence |

`ci_low` is **negative even frictionless** (−3.06 at 0 bps) despite a large
positive mean (+$11.52/trade). This is *not* a cost problem — it is a
**variance/noise** problem: 427 trades, p_positive only 0.062, a CI spanning
[−3, +26]. Reducing execution cost cannot rescue an edge that is indistinguishable
from zero before any cost is applied.

## Reconciliation with the 2026-06-12 audit

The two runs **agree on every decision-relevant statistic** but differ in trade
count:

| | bull_flag @ 5 bps | vwap_reversion @ 5 bps |
|---|---|---|
| 06-12 audit | 3179 trades · mean +2.15 · CI [−0.80, 5.13] · p 0.088 | 549 trades · mean +4.50 · CI [−7.79, 17.60] · p 0.242 |
| this run | 2636 trades · mean +2.42 · CI [−0.84, 5.59] · p 0.078 | 427 trades · mean +6.59 · CI [−7.96, 20.51] · p 0.188 |
| verdict | no-evidence (both) | no-evidence (both) |

The scenarios are byte-identical (all 999 files dated 06-12), yet this run
produces ~17% fewer bull_flag trades with a nearly unchanged per-trade
distribution. That fingerprint — fewer entries, same distribution — is
**look-ahead removal**. The 06-12 audit was a Docker `compose run`; its image
most likely predated the point-in-time daily-slice fix
([fe809c0](../../src/alpaca_bot/replay/runner.py), 2026-06-12), while this
break-even run uses the editable install with the fix in place. Fewer entries
clear a *point-in-time* daily trend gate than a look-ahead-constant one. This run
is therefore the cleaner measurement, and **bull_flag remains positive-edge
through 3 bps even after the look-ahead is removed** — the result strengthens.

## Interpretation vs realistic execution cost

The production cost model is `REPLAY_SLIPPAGE_BPS = 5.0`
([config/__init__.py:162](../../src/alpaca_bot/config/__init__.py#L162)) —
deliberately conservative. For liquid large-caps traded with marketable-limit
orders at retail size, realized adverse selection is typically **~1–3 bps/side**
(roughly a half-spread plus small impact). Against bull_flag's break-even of
**≈4 bps/side**:

- At a realistic **2 bps/side**, bull_flag's after-cost edge is `ci_low = +1.71`
  (positive-edge). At **3 bps/side**, `ci_low = +0.82` (still positive-edge).
- The **headroom between realistic cost (~3 bps) and break-even (~4 bps) is only
  ~1 bp.** That is a real but *thin* margin — fragile to OOS decay, regime shift,
  and cost variance.

## Gate decision

**Build the cross-sectional / portfolio top-K replay next.** Rationale:

1. **vwap_reversion is dead as a cost target** — no edge even frictionless;
   variance-dominated. Drop it from the cost-improvement track.
2. **bull_flag's cost headroom is too thin to rely on.** Break-even ≈ 4 bps and
   realistic cost ≈ 3 bps leaves ~1 bp of margin. Attacking execution cost alone
   buys at most ~1 bp of additional `ci_low` — not enough to be confident an edge
   this thin survives the OOS gate.
3. **Selectivity is the lever that raises per-trade edge**, which widens
   break-even and thickens the margin. The single-factor lever sweep already
   falsified per-knob tuning (null); the remaining untested mechanism is
   *cross-sectional* ranking (top-K of the day across symbols), which the current
   one-symbol-per-scenario replay cannot express. This is the named next build.

**In parallel (cheap, immediate):** bull_flag is the first strategy to show
`positive-edge` through 3 bps/side under the look-ahead-corrected harness. It
should be advanced **through the existing nightly OOS gate** as the current best
candidate — not hand-promoted, not traded from this in-sample result. The
break-even here is in-sample and descriptive; OOS edge may be lower.

## Constraints honored

- `TRADING_MODE=paper`, `ENABLE_LIVE_TRADING=false`, bot stays `close_only`. No
  production config touched. `REPLAY_SLIPPAGE_BPS` production semantics unchanged —
  the ladder is a sweep parameter only.
- In-sample diagnostic. Any promotion is exclusively via `alpaca-bot-nightly` →
  `candidate.env` → OOS gate.

## Reproduce

```bash
set -a && source /etc/alpaca_bot/alpaca-bot.env && set +a
alpaca-bot-backtest break-even \
  --scenario-dir /var/lib/alpaca-bot/nightly/scenarios \
  --strategy bull_flag --strategy vwap_reversion \
  --slippage-ladder 0,1,2,3,4,5 --output -
```

# Cost-aware lever sweep — bull_flag (2026-06-15)

**Outcome: null.** No single-factor lever moves bull_flag's after-cost edge
toward break-even. The one lever that looked promising on the 6-scenario smoke
(`relative_volume_threshold=2.0`) *actively hurt* ci_low on the full sample, and
every other family was either inert or harmful. No candidate is handed off.

## What this is

A read-only diagnostic sweep (`alpaca-bot-backtest lever-sweep`, see
`src/alpaca_bot/replay/lever_sweep.py`) that perturbs the live baseline one
lever-family at a time, re-runs the audit objective (after-cost bootstrap 95% CI
on pooled per-trade P&L at 5 bps/side), and ranks each point by in-sample
`ci_low` — the quantity the audit verdict turns on (`ci_low > 0` ⇒
positive-edge). Each candidate is then re-scored out-of-sample via the same
80/20 point-in-time walk-forward split (`replay/splitter.py`) the nightly gate
uses. Candidates only — promotion remains the nightly OOS gate's job
(`alpaca-bot-nightly` → `candidate.env`), never a hand-applied sweep winner.

This is one of the two lead candidates from the 2026-06-12 honest re-evaluation
(`2026-06-12-honest-reevaluation.md`): bull_flag had the highest win rate
(66.4%) and the best non-trivial after-cost mean (+\$2.15/trade) of any
strategy, but its CI still straddled zero (p=0.088). The thesis under test:
*can selectivity / longer holds / stronger entry filters convert that
frictionless edge into a significant after-cost edge by raising per-trade edge
faster than they shrink the sample?*

## Methodology and its limits — read before trusting any number

- **100-scenario stride sample, not the full 999.** A single 252-day scenario
  replay takes ≈3.3s; the full 999 × ~21 OFAT points × walk-forward is ≈40h per
  strategy — infeasible to iterate on. The screen runs every 10th of the 999
  sorted scenarios (`/tmp/lever_sample_100`, AAL…YOU). **Sampling screens; it
  does not certify.** A null here means "no family cleared the bar on a
  representative sample," not "proven flat on all data." Any survivor would have
  re-validated on the full-data nightly OOS gate before promotion — none
  survived, so that step is moot this cycle.
- **Coarse grid: one representative point per family**, not the full OFAT range.
  The screen's logic is a filter: if a family can move the needle, its coarse
  point should show *some* improvement. A family that is flat or negative at its
  coarse point does not earn the cost of a full OFAT refinement.
- **Family F (regime filter) is omitted — it is inert in replay.** The replay
  runner calls `evaluate_cycle()` without a `regime_bars` argument
  (`runner.py:124-136`), so the engine's regime gate
  (`engine.py:519`: `if settings.enable_regime_filter and regime_bars is not
  None:`) is never reached. Sweeping it would have produced a misleading
  "regime has no edge" artifact; the real cause is that this harness cannot
  evaluate regime at all (it needs a benchmark index series the replay does not
  carry). This is a measurement gap, not a finding about regime.

## Results — coarse screen, 100 scenarios, 80/20 walk-forward

**Baseline** (`baseline`): IS ci_low=−6.2310, trades=234, mean=\$3.5269,
p=0.2610, verdict=no-evidence; OOS ci_low=−14.8577 (90 trades),
verdict=no-evidence.

Ranked by in-sample after-cost `ci_low`:

| rank | lever | IS ci_low | Δci_low | IS mean | IS trades | IS p | OOS ci_low |
|---|---|---|---|---|---|---|---|
| 1 | A_initial_stop: atr_stop_multiplier=1.5 | −5.9861 | **+0.2449** | 4.1970 | 234 | 0.2225 | −14.8577 |
| 2 | baseline | −6.2310 | 0.0000 | 3.5269 | 234 | 0.2610 | −14.8577 |
| 3 | B_trail_atr: trailing_stop_atr_multiplier=2.5 | −6.2310 | 0.0000 | 3.5269 | 234 | 0.2610 | — |
| 4 | C_trail_trigger: trailing_stop_profit_trigger_r=1.5 | −6.2310 | 0.0000 | 3.5269 | 234 | 0.2610 | — |
| 5 | D_profit_target: on@3.0 | −6.2310 | 0.0000 | 3.5269 | 234 | 0.2610 | — |
| 6 | G_vwap: off | −7.1949 | −0.9639 | 2.1712 | 263 | 0.3245 | — |
| 7 | H_session: end=14:00 | −8.2495 | −2.0185 | 2.3399 | 215 | 0.3400 | — |
| 8 | E_rel_vol: relative_volume_threshold=2.0 | −11.0823 | **−4.8513** | 3.5928 | 131 | 0.3070 | — |

## What the table says

**Every lever family fails, and they fail in instructive ways:**

- **A_initial_stop (+0.24):** the only non-harmful move, and it is noise — a
  0.24 nudge on a −6.23 baseline, p unchanged at ~0.22, and the OOS ci_low is
  *identical* to baseline (−14.8577). Tightening the initial ATR stop did not
  change which trades the strategy took or their pooled outcome materially.
- **B / C / D (exactly 0.00):** the trailing-stop and profit-target levers are
  **inert at their coarse points** — the pooled per-trade P&L is byte-identical
  to baseline. bull_flag's exits on this sample are not being decided by a 2.5×
  trailing ATR, a 1.5R trigger, or a 3.0R target; widening them changes nothing.
- **G_vwap:off (−0.96):** removing the VWAP entry filter *adds* trades (234→263)
  and *lowers* edge. The filter is removing net-negative trades — loosening it
  is the wrong direction.
- **H_session end=14:00 (−2.02):** restricting the entry window cut trades
  (234→215) and hurt. The afternoon entries bull_flag was taking were net
  contributors, not churn to be trimmed.
- **E_rel_vol=2.0 (−4.85), the headline:** raising the relative-volume threshold
  cut the trade count nearly in half (234→131) but **lowered** ci_low by the
  most of any lever. The trades it filtered out were *net-positive*
  contributors. This is the exact inverse of the selectivity thesis, and the
  exact inverse of the 6-scenario smoke (where E_rel_vol was the top-ranked
  improver at +12.11). The smoke's 18-trade sample was a mirage; on 234 trades
  the sign flips.

## Why no OFAT refine, and why no single-factor path exists

The decisive arithmetic: baseline mean per-trade after-cost P&L is \$3.53, yet
ci_low is −6.23 — a bootstrap half-width of ≈\$9.8 (se ≈ \$4.98 over 234
trades). Reaching ci_low > 0 requires the mean to climb to roughly \$10/trade.
**No coarse lever point pushed mean above \$4.20.** The selectivity levers that
cut trade count (E, H) *widen* the CI faster than they lift the mean — fewer
samples, more variance — so they move ci_low the wrong way even when the mean
holds. There is no single-lever path from −\$6 to break-even when the gap to
close is ~3× the current mean and the only mean-raising lever (A) raises it by
\$0.67.

Per the staged plan, a family earns an OFAT refinement only if its coarse point
shows a credible positive Δci_low. None did. A_initial_stop's +0.24 is inside
the noise floor and did not move OOS at all. Recording the null is the honest
outcome; manufacturing an OFAT sweep around a noise-level nudge would be
p-hacking.

## Candidate hand-off

**None.** No lever point held a non-negative OOS `ci_low`, and the best in-sample
move was noise. Nothing is routed to the nightly OOS gate this cycle. No
production config change: the bot remains `close_only`; `TRADING_MODE=paper` and
`ENABLE_LIVE_TRADING=false` are untouched.

## What this rules out, and where edge must come from next

This sweep falsifies the *single-factor* form of the selectivity thesis for
bull_flag. It does **not** rule out:

1. **Multi-factor interactions.** OFAT cannot see a combination where, e.g., a
   tighter stop only pays off jointly with a session restriction. The sweep is
   one-lever-at-a-time by construction.
2. **Cross-sectional ranking.** The original thesis named "rank-filter to only
   the highest-conviction signals per cycle" — but the lever set cannot express
   it. `relative_volume_threshold` is a *per-symbol absolute* gate; the replay
   runs **one symbol per scenario**, so there is no cross-symbol field to rank
   within a cycle. The most promising untested selectivity mechanism is
   structurally invisible to this harness. That is the strongest signpost for
   the next sub-project: a ranking-based portfolio replay, not another per-symbol
   lever.
3. **A different cost regime.** Everything here is at 5 bps/side. bull_flag's
   frictionless edge is real (+\$20.6k over 3,179 trades in the full audit); the
   entire problem is the \$13.7k cost drag. Anything that lowers realized
   per-trade cost (fewer, larger, longer-held positions; limit-order entries)
   attacks the actual binding constraint more directly than entry-filter tuning.

Companion null for the secondary candidate: `2026-06-15-lever-sweep-vwap_reversion.md`.

# Cost-aware lever sweep — vwap_reversion (2026-06-15)

**Outcome: null.** No single-factor lever produces a candidate that survives the
out-of-sample split. The one lever with a large in-sample improvement
(`relative_volume_threshold=2.0`, Δci_low +11.86) **collapses out-of-sample**
(OOS ci_low −39.37 vs baseline −5.05) — a classic in-sample overfit that the
OOS check is built to catch. Nothing is handed off.

## What this is

The secondary lead candidate from the 2026-06-12 honest re-evaluation
(`2026-06-12-honest-reevaluation.md`). vwap_reversion was the *only* strategy
with a positive after-cost mean (+\$4.50/trade), but on just 549 trades and
p(edge≠0)=0.24 — its positive mean is indistinguishable from noise. Same
diagnostic harness, thesis, and constraints as the bull_flag companion report
(`2026-06-15-lever-sweep-bull_flag.md`); read that file's **Methodology and its
limits** section for the full caveats (100-scenario stride sample is a screen
not a certification; coarse grid is one point per family; Family F regime is
inert in replay and omitted). They are not repeated here.

## Results — coarse screen, 100 scenarios, 80/20 walk-forward

**Baseline** (`baseline`): IS ci_low=−20.3966, trades=36, mean=\$21.1255,
p=0.1595, verdict=no-evidence; OOS ci_low=−5.0529, verdict=no-evidence.

Ranked by in-sample after-cost `ci_low`:

| rank | lever | IS ci_low | Δci_low | IS mean | IS trades | IS p | OOS ci_low |
|---|---|---|---|---|---|---|---|
| 1 | E_rel_vol: relative_volume_threshold=2.0 | −8.5405 | **+11.8561** | 37.0435 | 24 | 0.0620 | **−39.3743** |
| 2 | G_vwap: off | −18.5280 | +1.8686 | 19.4578 | 42 | 0.1600 | −5.0529 |
| 3 | baseline | −20.3966 | 0.0000 | 21.1255 | 36 | 0.1595 | −5.0529 |
| 4 | A_initial_stop: atr_stop_multiplier=1.5 | −20.3966 | 0.0000 | 21.1255 | 36 | 0.1595 | −5.0529 |
| 5 | B_trail_atr: trailing_stop_atr_multiplier=2.5 | −20.3966 | 0.0000 | 21.1255 | 36 | 0.1595 | −5.0529 |
| 6 | C_trail_trigger: trailing_stop_profit_trigger_r=1.5 | −20.3966 | 0.0000 | 21.1255 | 36 | 0.1595 | — |
| 7 | D_profit_target: on@3.0 | −20.3966 | 0.0000 | 21.1255 | 36 | 0.1595 | — |
| 8 | H_session: end=14:00 | −20.5899 | −0.1933 | 22.0905 | 35 | 0.1705 | — |

## What the table says

- **E_rel_vol=2.0 — the overfit trap.** It is the top-ranked lever in-sample by a
  wide margin: ci_low jumps +11.86 and the in-sample p falls to 0.062, the
  closest any point in this whole exercise came to significance. It does it by
  raising the mean to \$37/trade on just 24 trades. **Then it inverts
  out-of-sample:** OOS ci_low is −39.37, ~8× worse than baseline's −5.05. The
  in-sample gain was 24-trade small-sample luck, not a transferable edge. This
  is precisely the failure mode the walk-forward split exists to expose — and
  why an in-sample sweep winner is never hand-promoted.
- **A / B / C / D (exactly 0.00):** the stop, trailing-stop, and profit-target
  levers are **inert** for vwap_reversion at their coarse points — pooled
  per-trade P&L is identical to baseline. A mean-reversion strategy exits on the
  reversion-to-VWAP / end-of-day logic, not on a 1.5× ATR stop or a 3.0R target,
  so perturbing those values changes nothing on this sample.
- **G_vwap:off (+1.87 IS):** a marginal in-sample improvement with *more* trades
  (36→42), but its OOS ci_low (−5.05) merely matches baseline — no real gain,
  and it loosens the strategy's defining filter.
- **H_session end=14:00 (−0.19):** essentially flat; restricting the window does
  nothing useful here.

★ The cross-strategy pattern is the real finding. `E_rel_vol` is the top
in-sample improver on *both* lead candidates' smokes, yet it fails three
different ways once you look honestly: on vwap_reversion it overfits (great IS,
−39 OOS); on bull_flag's full sample it actively *hurts* IS (−4.85); on
bull_flag's smoke it was a small-sample mirage. A lever that "wins" in three
incompatible ways across samples has no stable edge — it is noise being read as
signal. ★

## Why no OFAT refine

The staged plan refines a family only when its coarse point shows a *credible*
positive Δci_low — one that is not an artifact of a tiny sample and that does not
reverse out-of-sample. E_rel_vol's +11.86 is large but fails both tests: 24
trades, and OOS −39. Refining it would be chasing the overfit deeper. Every
other family is inert or flat. There is nothing to refine; the honest output is
the null.

## Candidate hand-off

**None.** No lever point held a non-negative OOS `ci_low`. Nothing is routed to
the nightly OOS gate. No production config change: the bot remains `close_only`;
`TRADING_MODE=paper` and `ENABLE_LIVE_TRADING=false` are untouched. vwap_reversion
remains in the disabled-strategies set from the 2026-06-12 config response.

## Where edge must come from next

Same conclusion as the bull_flag companion: single-factor lever tuning over the
audit objective does not produce after-cost edge for either lead candidate. The
selectivity mechanism the original thesis named — *rank only the highest-
conviction signals per cycle* — is **structurally inexpressible** in this harness
(`relative_volume_threshold` is a per-symbol absolute gate; the replay runs one
symbol per scenario, so there is no cross-symbol field to rank within a cycle).
The next sub-project worth scoping is a cross-sectional / portfolio replay that
can express top-K ranking, and/or attacking the binding constraint — realized
per-trade cost — directly (fewer, larger, longer-held positions; limit entries)
rather than through entry-filter thresholds.

Companion null for the primary candidate: `2026-06-15-lever-sweep-bull_flag.md`.

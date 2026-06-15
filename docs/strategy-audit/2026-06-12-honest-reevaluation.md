# Honest re-evaluation — post harness fix (2026-06-12)

Harness defects fixed (commits `e33eb87`, `fe809c0`): scenario symbols now
reach the engine (`ReplayRunner.run()` passes `symbols=(scenario.symbol,)` to
`evaluate_cycle()`), and the daily series is sliced point-in-time per session
day (`b.timestamp.astimezone(market_timezone).date() < day`, recomputed when the
session day changes) instead of the full end-anchored series being handed to
every intraday bar.

This is the re-run the 2026-06-11 contrarian audit demanded before any
backtest-derived parameter could be trusted. It supersedes that audit's
ten "insufficient-data" rows, which were harness artifacts, not measurements.

## Audit — 5 bps/side, 999 scenarios

Source: `/var/lib/alpaca-bot/nightly/audit-2026-06-12.md` (run 2026-06-13,
`alpaca-bot-backtest audit --scenario-dir /data/scenarios --slippage-bps 5`).
Mirror committed alongside this file as `2026-06-12-audit-table.md`.

| strategy | scenarios | trades | win rate | profit factor | total P&L | mean/trade | ann. Sharpe | 95% CI mean/trade | p(edge≠0) | frictionless P&L | cost drag | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| breakout | 999 | 8195 | 52.7% | 0.78 | -30990.78 | -3.7817 | -4.82 | [-4.8380, -2.6577] | 1.0000 | 4164.40 | 35155.18 | **negative-edge** |
| momentum | 999 | 24167 | 56.2% | 0.92 | -35815.90 | -1.4820 | -1.96 | [-2.2842, -0.7240] | 1.0000 | 79930.37 | 115746.27 | **negative-edge** |
| orb | 999 | 34504 | 59.2% | 0.98 | -13149.46 | -0.3811 | -0.46 | [-0.9783, 0.2401] | 0.8845 | 147818.42 | 160967.88 | **no-evidence** |
| high_watermark | 999 | 52 | 55.8% | 0.57 | -525.41 | -10.1040 | -4.24 | [-26.3812, 5.0408] | 0.8930 | -299.93 | 225.48 | **no-evidence** |
| ema_pullback | 999 | 15357 | 52.4% | 0.92 | -19873.94 | -1.2941 | -2.01 | [-2.0501, -0.4775] | 1.0000 | 51231.92 | 71105.86 | **negative-edge** |
| vwap_reversion | 999 | 549 | 55.4% | 1.09 | 2469.90 | 4.4989 | 0.70 | [-7.7888, 17.6001] | 0.2420 | 5071.78 | 2601.88 | **no-evidence** |
| gap_and_go | 999 | 5 | 60.0% | 3.91 | 375.46 | 75.0928 | 5.15 | [-53.1731, 280.0505] | 0.3105 | 398.94 | 23.48 | **no-evidence** |
| bull_flag | 999 | 3179 | 66.4% | 1.08 | 6848.72 | 2.1544 | 1.03 | [-0.8044, 5.1284] | 0.0880 | 20560.08 | 13711.36 | **no-evidence** |
| vwap_cross | 999 | 13740 | 56.5% | 0.96 | -10099.45 | -0.7350 | -0.85 | [-1.7201, 0.2752] | 0.9250 | 54961.83 | 65061.28 | **no-evidence** |
| bb_squeeze | 999 | 8435 | 54.7% | 0.84 | -20034.81 | -2.3752 | -3.07 | [-3.3994, -1.3704] | 1.0000 | 20140.65 | 40175.46 | **negative-edge** |
| failed_breakdown | 999 | 1827 | 56.4% | 0.99 | -451.80 | -0.2473 | -0.13 | [-2.9422, 2.7800] | 0.5610 | 8341.40 | 8793.20 | **no-evidence** |

**Headline:** zero strategies are positive-edge. Four are negative-edge with
95% confidence (CI entirely below zero): breakout, momentum, ema_pullback,
bb_squeeze. The remaining seven are no-evidence (CI straddles zero), and six of
those seven have a negative point estimate — the lone exception, vwap_reversion,
has p(edge≠0) = 0.24, i.e. its small positive mean is indistinguishable from
noise.

**The recurring story is cost drag, not bad entries.** Nine of eleven
strategies are *profitable frictionless* (positive `frictionless P&L`) yet are
dragged to break-even or loss by 5 bps/side. orb frictionless makes +$147.8k
but pays $161.0k in costs; momentum makes +$79.9k frictionless and pays $115.7k.
These are high-churn strategies whose per-trade edge is smaller than the
round-trip cost. breakout — the live strategy — is the worst case: it barely
clears frictionless (+$4.2k over 8,195 trades, ~$0.51/trade gross) and loses
$3.78/trade after costs.

## Delta vs 2026-06-11 audit

The 06-11 run measured only momentum; the other ten rows were zero-trade
harness artifacts (the runner never passed the scenario symbol, so the engine
iterated `settings.symbols` and evaluated 8/999 scenarios; and the look-ahead
daily gate blocked every trend-filtered strategy because all 8 watchlist
symbols closed the window below their SMA20).

| strategy | 06-11 verdict | 06-12 verdict | 06-11 trades | 06-12 trades | note |
|---|---|---|---|---|---|
| breakout | insufficient-data | **negative-edge** | 0 | 8195 | Now measurable; live strategy. Negative-edge with 95% confidence. |
| momentum | negative-edge | **negative-edge** | 313 | 24167 | Verdict *confirmed and tightened*. 77× more trades; CI narrowed from [-6.23, -0.98] to [-2.28, -0.72]. The 991 previously-unevaluated symbol-years did not rescue it. |
| orb | insufficient-data | no-evidence | 0 | 34504 | Highest churn; biggest absolute cost drag ($161k). |
| high_watermark | insufficient-data | no-evidence | 0 | 52 | Too few trades to conclude; negative point estimate. |
| ema_pullback | insufficient-data | **negative-edge** | 0 | 15357 | Profitable frictionless (+$51k), negative after costs. |
| vwap_reversion | insufficient-data | no-evidence | 0 | 549 | Only positive after-cost mean, but p(edge≠0)=0.24 — noise. |
| gap_and_go | insufficient-data | no-evidence | 0 | 5 | 5 trades total; no signal. |
| bull_flag | insufficient-data | no-evidence | 0 | 3179 | Highest win rate (66.4%) and best non-trivial after-cost mean (+$2.15), but CI includes zero (p=0.088). Closest thing to a candidate. |
| vwap_cross | insufficient-data | no-evidence | 0 | 13740 | Negative point estimate; CI just crosses zero. |
| bb_squeeze | insufficient-data | **negative-edge** | 0 | 8435 | Negative-edge with 95% confidence. |
| failed_breakdown | insufficient-data | no-evidence | 0 | 1827 | Essentially flat (-$0.25/trade); CI wide. |

momentum is the cross-check that validates the fix: it was the one strategy the
06-11 harness measured correctly (its evaluator reconstructs point-in-time daily
bars itself), and its verdict is unchanged after the fix exposed it to 77× the
trade count. The harness fix did not invent a more favorable world — it
confirmed the one honest verdict we already had and extended that honesty to the
other ten strategies.

## Comparison vs the void R7 nightly results

The R7 nightly sweep (999 scenarios × 11 strategies × 80/20 walk-forward,
23.8h compute, finished 2026-06-12) accepted **0 of 11** candidates and wrote no
`candidate.env` (`nightly_sweep_completed` payload: `candidates_accepted: 0,
candidate_env_written: false`). At the time that read as the OOS gate working
under honest scoring.

This re-evaluation reinterprets that result:

- **For the 10 non-momentum strategies, R7's rejection was void, not earned.**
  The sweep uses the same `ReplayRunner` (`tuning/sweep.py`), so it inherited
  both harness defects: 991/999 scenarios were never evaluated, and the eight
  that were got a scenario-constant, look-ahead trend gate. The optimizer
  searched flat-zero objective functions for ten strategies. "No candidate
  found" meant "nothing was ever actually tested."
- **For momentum, R7's rejection was genuine** and is now corroborated: the
  06-12 audit independently measures momentum at negative-edge with 95%
  confidence. There was nothing for the optimizer to find.
- **The honest re-run does not contradict any R7 candidate**, because R7
  produced none. There are no R7 parameters to promote or refute — the question
  is moot. Any future parameter search must run on the fixed harness; all sweep
  results predating commit `fe809c0` are regime-coupled noise and are discarded.

## Sweep results

Re-swept the no-evidence strategies with a meaningful trade count
(`alpaca-bot-sweep --scenario-dir /data/scenarios --strategy <name>` on the
fixed harness): orb, vwap_cross, bull_flag, failed_breakdown, vwap_reversion.

**Not swept:** the four negative-edge strategies (breakout, momentum,
ema_pullback, bb_squeeze — no positive edge to tune toward); and gap_and_go
(5 trades) and high_watermark (52 trades), whose trade counts are too low for a
grid search to produce anything but overfit noise.

**Status: running in the background** (`/tmp/sweep-<strategy>-2026-06-12.txt`).
Each sweep grids parameters across all 999 scenarios on the fixed harness; the
host is concurrently running other nightly containers, so wall-clock is in
hours. Per-strategy best-parameter findings will be appended here as each sweep
completes.

**These sweeps cannot change the config response below**, and the decision was
not gated on them:

- No strategy measured positive-edge in the audit, so none qualifies for
  promotion this cycle. A swept parameter set that *appeared* to cross into
  positive after-cost edge in-sample would still be only a *candidate* — it
  could be promoted solely through the nightly OOS walk-forward gate
  (`alpaca-bot-nightly` → `candidate.env`), which is a separate operator
  decision requiring its own plan-and-refine cycle. It would not be hand-applied
  here.
- The live strategy (breakout) is negative-edge and was set to close-only on its
  audit verdict alone; re-sweeping breakout is explicitly out of scope (no
  positive edge to tune toward).

In other words, the sweeps are diagnostic input for the *next* tuning cycle, not
a dependency of this one.

## Config response decided (see Phase 3 / Task 6)

The evidence-gated config response is recorded separately in the following
commit. See the AuditEvent appended by the admin flow for the authoritative
record of the action taken.

# Break-even slippage / cost-sensitivity analysis — design

**Date:** 2026-06-15
**Status:** spec (brainstorm complete)
**Author:** agent team (autonomous /plan-and-refine)

## Problem

Every honest audit to date names the same binding constraint: **cost drag**, not signal
quality. From `docs/strategy-audit/2026-06-12-honest-reevaluation.md`, 9 of 11 strategies
are profitable *frictionless* but dragged to break-even-or-loss by 5 bps/side of modelled
slippage. The two lead candidates:

- **bull_flag** — +$6.47/trade frictionless, +$2.15 after cost, full-999 audit
  ci_low = −0.80 at 5 bps (p = 0.088). *Almost* significant-positive; the crossing is just
  below 5 bps.
- **vwap_reversion** — +$4.50/trade after cost but p = 0.24 on 549 trades.

The 2026-06-15 single-factor lever sweep (`docs/strategy-audit/2026-06-15-lever-sweep-*.md`)
returned a clean null: **no single entry/stop/hold lever recovers after-cost edge** for
either candidate, and the most-promising lever (`relative_volume_threshold`) fails three
incompatible ways across samples. Single-factor tuning over the audit objective is
falsified as a path.

That leaves two live hypotheses, both expensive to build:

1. **Lower realized cost** — fewer/larger/longer-held positions, limit entries.
2. **Cross-sectional selectivity** — top-K ranking per cycle (the thesis's named mechanism,
   structurally inexpressible in today's one-symbol-per-scenario replay).

Before committing to either build, we need one number per strategy: **the break-even
slippage** — the bps/side at which the after-cost bootstrap `ci_low` crosses zero. That
number tells us whether the gap is "shave a couple bps off execution" (cheap, attack cost
directly) or "no plausible execution improvement closes it" (must build the much more
expensive cross-sectional portfolio replay).

## Goal

A read-only diagnostic that scores each lead candidate across a **slippage ladder**
(default `{0,1,2,3,4,5}` bps/side) on the full 999-scenario history, and reports — per
strategy — the after-cost `ci_low` at each rung plus the **interpolated break-even bps**.
Candidates / diagnostics only: **no production config change**, bot stays `close_only`,
`TRADING_MODE=paper` and `ENABLE_LIVE_TRADING=false` untouched.

## Why re-run per level (Approach B), not analytical (Approach A)

A tempting shortcut: run the replay once frictionless, then subtract a per-trade cost
linear in bps to derive every rung from one pass (~6× cheaper). **This is unsound.**
Slippage is not a linear per-trade deduction:

- **Entry fill is capped at the limit price** — `runner.py:233`:
  `fill_price = min(self._slipped(fill_price, side="buy"), order.limit_price)`. At high
  bps the cap binds, so entry cost is piecewise-nonlinear in bps.
- **Quantity is derived from the slipped fill** — `runner.py:233-238`:
  `calculate_position_size(entry_price=fill_price, ...)`. A different fill ⇒ a different
  share count ⇒ the whole trade's P&L scales differently.
- **Profit-target level is derived from the slipped entry** — `runner.py:317-319`:
  `target_price = entry_price + profit_target_r * risk_per_share`. Shifting the target
  shifts *which bar* triggers `bar.high >= target_price`, so the trade *set itself*
  changes with slippage.

Because the trade set is not slippage-invariant, the only defensible method is to **re-run
the replay at each slippage level** and bootstrap `ci_low` on that level's actual pooled
trades. The ladder's `s = 0` rung doubles as the frictionless reference, so we call the
replay exactly once per rung (no redundant frictionless pass — unlike `run_audit`, which
runs both regimes every call).

## Why the full 999 scenarios, not the 100-stride sample

The lever sweep used `/tmp/lever_sample_100` (every 10th of 999) as a *screen*. That sample
is **not representative for this question**: bull_flag baseline `ci_low` is −6.23 on the
100-sample but −0.80 on the full 999. The break-even is a single descriptive scalar per
strategy with **no parameter fit and no model selection**, so there is no overfitting risk
to mitigate by sampling — the only cost of using all data is compute, and this is a one-shot
background run. The number must come from the full data to be trustworthy.

## Why no walk-forward / OOS split

The lever sweep needed an 80/20 walk-forward because it *selected* a lever value and had to
guard against in-sample overfit. Break-even slippage selects nothing: it is a deterministic
function of the pooled per-trade P&L distribution at each cost level. There is no parameter
to overfit and no multiple-comparisons exposure, so an OOS split would only halve the sample
and widen the CIs without testing anything. The honest framing is "on the full 999-scenario
history, after-cost `ci_low` crosses zero at ≈ X bps" — an in-sample descriptive statistic,
caveated as such.

## Components

### `src/alpaca_bot/replay/break_even.py` (new)

```
DEFAULT_SLIPPAGE_LADDER: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)

@dataclass(frozen=True)
class BreakEvenPoint:
    slippage_bps: float
    trades: int
    mean_trade_pnl: float | None
    total_pnl: float
    ci_low: float | None
    ci_high: float | None
    p_positive: float | None
    verdict: str

@dataclass(frozen=True)
class BreakEvenResult:
    strategy: str
    scenarios: int
    points: tuple[BreakEvenPoint, ...]      # ascending by slippage_bps
    break_even_bps: float | None            # interpolated zero-crossing; see semantics below

def run_break_even_sweep(*, scenarios, settings, strategy,
                         slippage_ladder=DEFAULT_SLIPPAGE_LADDER,
                         pooled_trades_fn=_replay_pooled_trades,
                         on_progress=None) -> BreakEvenResult
def _interpolate_break_even(points) -> float | None
def format_break_even_markdown(results) -> str
```

`run_break_even_sweep` loop, per rung `bps` in `sorted(slippage_ladder)`:
1. `costed = dataclasses.replace(settings, replay_slippage_bps=bps)` (local copy; production
   `REPLAY_SLIPPAGE_BPS` is never touched).
2. `trades = pooled_trades_fn(scenarios, costed, strategy)`.
3. `pnls = [t.pnl for t in trades]`; `ci = bootstrap_mean_ci(pnls)`;
   `p = bootstrap_p_positive(pnls)` (None when `len(pnls) < MIN_SAMPLES`).
4. `verdict = classify_verdict(trades=len(pnls), ci=ci, p_positive=p)` — reused verbatim
   from `audit.py`.
5. Append `BreakEvenPoint`.

It reuses `classify_verdict`, `bootstrap_mean_ci`, `bootstrap_p_positive`, `MIN_SAMPLES`,
`_replay_pooled_trades`, and the `PooledTradesFn` seam — all imported from
`alpaca_bot.replay.audit` / `.stats`. No modification to those modules.

### `_interpolate_break_even` semantics

Points are ascending in bps; `ci_low(s)` is approximately (not strictly) monotone-decreasing.
Scan adjacent pairs and return the **first** (lowest-bps) crossing from `ci_low > 0` to
`ci_low <= 0`, linearly interpolated:

```
be = lo.bps + (hi.bps - lo.bps) * lo.ci_low / (lo.ci_low - hi.ci_low)
```

Boundary cases:
- First rung (`s = 0`) `ci_low <= 0` → return `0.0` (no significant edge even frictionless).
- First rung `ci_low is None` (insufficient trades) → return `None`.
- All rungs `ci_low > 0` → return `None` (break-even beyond the ladder; report flags
  "> max rung — extend ladder").
- A rung with `ci_low is None` mid-ladder is skipped as an interpolation endpoint; if no
  valid bracketing pair exists, return `None`.

Taking the first crossing is conservative w.r.t. the tiny non-monotonicity introduced by the
slippage-dependent trade set.

### CLI subcommand — `src/alpaca_bot/replay/cli.py`

`alpaca-bot-backtest break-even`:
- `--scenario-dir DIR` (required) — glob `*.json`, load via `ReplayRunner.load_scenario`.
- `--strategy NAME` (repeatable; `choices=STRATEGY_REGISTRY`; default `bull_flag` +
  `vwap_reversion`).
- `--slippage-ladder "0,1,2,3,4,5"` (comma-separated floats; default
  `DEFAULT_SLIPPAGE_LADDER`).
- `--output FILE` (default `-` / stdout), mirroring the `audit` subcommand's
  `_write_output`.
- Emits per-strategy progress to stderr via `on_progress`, mirroring `audit`/`lever-sweep`.

### Report — `docs/strategy-audit/2026-06-15-break-even-slippage.md`

One section per strategy: the ladder table (bps | trades | mean | ci_low | ci_high | p |
verdict), the interpolated break-even bps, and an interpretation against realistic
large-cap limit-order execution cost. Plus a cross-strategy synthesis and the gate decision:
does break-even sit close enough to a plausible realized cost to justify attacking cost
directly, or is it low enough that only cross-sectional selectivity could close the gap?

## Testing (TDD, fakes not mocks)

`tests/unit/test_break_even.py`, fake `pooled_trades_fn` that returns synthetic
`ReplayTradeRecord`s whose `pnl` decreases with `settings.replay_slippage_bps` (mirroring
real cost behaviour) so the ladder and interpolation are exercised deterministically:

- Ladder runs once per rung, ascending, with the injected slippage threaded through.
- Interpolation returns the known analytic crossing for a constructed `ci_low` sequence.
- Boundary: all-positive → None; frictionless-negative → 0.0; insufficient-trades → None.
- Verdict reuse matches `classify_verdict` for representative inputs.
- `format_break_even_markdown` renders the table, break-even row, and handles None.

No broker, no Postgres, no network — pure in-memory, consistent with the project's DI
convention.

## Safety / constraints

- **Pure offline read-only diagnostic.** No order submission, no position sizing in
  production, no stop placement, no broker calls, no market-hours interaction.
- **No `AuditEvent`** — no runtime state changes.
- **No migration, no new env var.** The slippage ladder is a CLI argument applied via
  `dataclasses.replace` on a *local* Settings copy; the production `REPLAY_SLIPPAGE_BPS`
  default and semantics are unchanged.
- **`evaluate_cycle()` stays pure** — unchanged.
- **Paper/live untouched.** No code path can reach the broker; `ENABLE_LIVE_TRADING=false`
  and `TRADING_MODE=paper` remain effective gates. Bot stays `close_only`.
- **Candidates only.** Any survivor is routed to the nightly OOS gate
  (`alpaca-bot-nightly` → `candidate.env`), never hand-applied. This diagnostic produces no
  survivor to promote — it informs the *next* sub-project's build decision.

## Out of scope (YAGNI)

- The full no-evidence strategy set (start with the two lead candidates; extend only if the
  pair's break-even is informative enough to warrant it).
- Walk-forward / OOS (justified above).
- Any change to `run_audit`, `lever_sweep`, or production config.
- Building the cross-sectional portfolio replay — this diagnostic *gates* that decision; it
  does not start it.

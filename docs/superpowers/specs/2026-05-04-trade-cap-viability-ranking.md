# Trade Cap and Viability Ranking Design

## Goal

Two related improvements to the tuning/sweep pipeline:

1. **max_trades gate** — a ceiling complement to `min_trades` in `score_report()`. Disqualifies candidates that over-trade (e.g., >20 trades per scenario), which is a red flag for hyperactive or curve-fitted strategies.

2. **Viability composite ranking** — replaces the raw IS-Sharpe-only sort in sweep results with a richer tuple key `(has_score, score, r_multiple, win_rate)`. Ensures the "best" candidate uses trade quality (R-multiple) as a tie-breaker, not just Sharpe rank.

---

## Architecture

### Feature 1: max_trades gate

Mirrors `max_drawdown_pct` exactly: a new `max_trades: int = 0` kwarg in `score_report()` (0 = disabled). When `max_trades > 0` and `report.total_trades > max_trades`, return `None`. Propagated through:

- `run_sweep()`
- `run_multi_scenario_sweep()`
- `evaluate_candidates_oos()`

and exposed as `--max-trades` (default 0) in both `tuning/cli.py` and `nightly/cli.py`.

The gate fires **per scenario**, not on the aggregated total — each scenario must individually be within the cap. This matches how `min_trades_per_scenario` already works.

### Feature 2: _viability_key composite ranking

A new function in `sweep.py`:

```python
def _viability_key(candidate: TuningCandidate, oos_score: float | None = None) -> tuple:
```

Returns a 4-tuple for lexicographic comparison:
1. `has_score: bool` — None-scored candidates always sort last (preserves existing behaviour)
2. `score: float` — IS score (or OOS score when supplied) — primary ranking
3. `r_multiple: float` — avg_win / |avg_loss|; `10.0` when no losers (pure-win streak)
4. `win_rate: float` — fraction of winning trades

This replaces `lambda c: (c.score is not None, c.score or 0.0)` in both `run_sweep()` and `run_multi_scenario_sweep()` sort calls.

It is also used in both CLIs to pick `best` in the walk-forward path:
- Old: `max(held_pairs, key=lambda pair: pair[1])`  (pure OOS score)
- New: `max(held_pairs, key=lambda pair: _viability_key(pair[0], pair[1]))`  (OOS score + quality)

For the non-walk-forward path, no CLI change is needed: `scored[0]` already gets the top viability candidate since the sort itself uses `_viability_key`.

---

## Data Flow

```
score_report(report, *, min_trades, max_drawdown_pct, max_trades)
        ↓
run_sweep / run_multi_scenario_sweep / evaluate_candidates_oos
  — all propagate max_trades
  — sweeps sort final list by _viability_key(c)
        ↓
CLI best-selection (non-WF path)
  scored[0] → already viability-ranked ✓
        ↓
CLI best-selection (WF path)
  max(held_pairs, key=lambda pair: _viability_key(pair[0], pair[1]))
```

---

## Design Decisions

**Why 0 as default, not 20?**
Matches `max_drawdown_pct` convention. The user chooses their cap. A 252-day breakout scenario might yield 8–25 trades depending on settings — a hardcoded default of 20 could over-filter aggressively. The user passes `--max-trades 20` explicitly.

**Why lexicographic tuple (not multiplicative composite)?**
Sharpe still dominates: `(score=1.0, r=2.0) > (score=0.9, r=5.0)`. This avoids changing the primary ranking for well-separated candidates while ensuring quality determines tie-breaks. Less surprising, easier to reason about.

**Why R-multiple = 10.0 for no-losers?**
When `avg_loss_return_pct is None` (all trades won), the strategy has unlimited R. Capping at 10.0 rewards pure-win candidates without making them pathologically dominant. 10 > any realistic R for a real strategy.

**Why apply gate per scenario, not aggregate?**
In multi-scenario sweeps, a strategy that fires 5 times per scenario across 3 scenarios has 15 aggregate trades but only 5 per scenario. Applying the gate per scenario maintains the intended semantics: "this parameter set shouldn't fire more than N times in any single measured period."

**No change to evaluate_cycle().**
This feature touches only the tuning pipeline. `evaluate_cycle()` remains a pure function with no new dependencies.

**No new env vars.**
`max_trades` is a CLI flag only, not a runtime setting.

---

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/tuning/sweep.py` | Add `max_trades` to `score_report()`, propagate to all 3 sweep functions, add `_viability_key()`, update both sort lambdas |
| `src/alpaca_bot/tuning/cli.py` | Add `--max-trades`, wire `max_trades`, update WF `max()` to use `_viability_key` |
| `src/alpaca_bot/nightly/cli.py` | Add `--max-trades`, wire `max_trades`, update WF `max()` to use `_viability_key` |
| `tests/unit/test_tuning_sweep.py` | Update `fake_score` signatures, add tests for `max_trades` gate and `_viability_key` |
| `tests/unit/test_tuning_sweep_cli.py` | Test `--max-trades` forwarded, update `fake_score` signatures |
| `tests/unit/test_nightly_cli.py` | Test nightly WF picks by viability (R-multiple tie-break) |

---

## Test Plan

- `test_score_report_disqualifies_on_excessive_trades`: max_trades=5, total=6 → None; total=4 → not None
- `test_score_report_max_trades_zero_disabled`: max_trades=0, total=100 → not None (gate off)
- `test_viability_key_uses_r_multiple_as_tiebreaker`: equal IS scores, higher R wins
- `test_viability_key_no_losers_gets_high_r`: avg_loss=None → r=10.0
- `test_viability_key_none_score_sorts_last`: has_score=False always < has_score=True
- `test_evolve_max_trades_passed_to_sweep`: `--max-trades 5` forwarded as `max_trades=5`
- `test_nightly_viability_tiebreak_picks_higher_r`: two held candidates with equal OOS score → picks higher R-multiple

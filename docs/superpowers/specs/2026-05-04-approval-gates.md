# Spec: Tighter Approval Gates for `alpaca-bot-evolve`

**Date:** 2026-05-04  
**Branch:** stop-order-reliability-fixes

---

## Problem

The `alpaca-bot-evolve` candidate selection has three gaps that allow net-losing or overfit parameters to be approved for live trading:

1. **Negative-score candidates can win.** `score_report()` returns negative values when Sharpe/Calmar is negative. If every candidate in a run has a negative score, `scored[0]` is a net-losing strategy — "best of the worst" still gets approved.

2. **Profit factor < 1.0 is only penalized, not blocked.** A strategy with `profit_factor=0.7` (loses 30 cents per dollar won) gets a score-multiplied-down but can still become `best`. The intent was always to disfavor net-losing strategies; a hard gate is more defensible.

3. **Walk-forward validation is display-only.** When `--validate-pct` is used, OOS "held" results are printed but `best` is still `scored[0]` — the IS-best candidate regardless of OOS performance. The walk-forward feature has zero effect on which parameters get approved.

---

## Fix

### Change 1 — Hard score floor in `score_report()`

In `tuning/sweep.py::score_report()`:

1. Replace the `profit_factor < 1.0` multiplicative penalty with a hard `return None`:
   - Before: `base *= profit_factor`
   - After: `return None`  (net-losing strategies disqualified the same as too-few-trades)

2. Add a score floor: if `base <= 0.0` after sharpe/calmar computation, `return None`.
   - Negative Sharpe = net-losing direction; zero Sharpe = no edge. Neither should be deployed.
   - `profit_factor is None` (no losses at all) remains unpunished — all-winner strategies pass.

```python
def score_report(report: BacktestReport, *, min_trades: int = 3) -> float | None:
    if report.total_trades < min_trades:
        return None
    if report.sharpe_ratio is not None:
        base = report.sharpe_ratio
    elif report.mean_return_pct is None:
        return None
    else:
        drawdown = report.max_drawdown_pct or 0.0
        base = report.mean_return_pct / (drawdown + 0.001)
    if report.profit_factor is not None and report.profit_factor < 1.0:
        return None  # net-losing: disqualify
    if base <= 0.0:
        return None  # non-positive Sharpe/Calmar: disqualify
    return base
```

### Change 2 — Walk-forward selection gate in `tuning/cli.py`

When `--validate-pct` is used, override `best` with the **highest-OOS-scoring held candidate** (among the top-10 IS candidates):

- "Held" definition unchanged: `oos_score >= is_score * 0.5`
- If no held candidates exist: print an informative message and return 1 (no env block produced)
- `_save_to_db` is still called with all IS candidates (DB is for research; gate only affects env-block output)

```python
if validate_pct > 0.0 and oos_scenarios:
    top10 = scored[:10]
    oos_scores = evaluate_candidates_oos(...)
    _print_walk_forward_block(top10, oos_scores, ...)
    held_pairs = [
        (c, s) for c, s in zip(top10, oos_scores)
        if s is not None and c.score is not None and s >= c.score * 0.5
    ]
    if not held_pairs:
        print("\nNo walk-forward held candidates — approval gate blocked all.")
        return 1
    best = max(held_pairs, key=lambda pair: pair[1])[0]
```

---

## Files Changed

| File | Change |
|---|---|
| `src/alpaca_bot/tuning/sweep.py` | `score_report()`: replace profit_factor penalty with hard gate; add `base <= 0` gate |
| `src/alpaca_bot/tuning/cli.py` | `main()`: override `best` from held OOS candidates when `--validate-pct` used |
| `tests/unit/test_tuning_sweep.py` | Update `test_score_report_penalizes_subunit_profit_factor` → expects None; add score-floor tests |
| `tests/unit/test_tuning_sweep_cli.py` | Add 2 new walk-forward gate tests |

---

## Affected Tests (existing)

| Test | Before | After |
|---|---|---|
| `test_score_report_penalizes_subunit_profit_factor` | expects `score ≈ 1.4` | expects `None` |
| `test_score_report_no_penalty_when_profit_factor_at_or_above_one` | expects `2.0` | unchanged |
| `test_score_report_no_penalty_when_profit_factor_none` | expects `3.0` | unchanged |

---

## New Tests

| Test | Purpose |
|---|---|
| `test_score_report_profit_factor_below_one_disqualifies` | `profit_factor=0.7` → None |
| `test_score_report_nonpositive_sharpe_disqualifies` | Sharpe=0.0 → None; Sharpe=-0.5 → None |
| `test_score_report_positive_sharpe_above_floor_passes` | Sharpe=0.001 → passes gate |
| `test_walk_forward_gate_selects_best_oos_held_candidate` | Held candidates → best is max OOS score |
| `test_walk_forward_gate_exits_nonzero_when_no_held_candidates` | No held → returns 1 |

---

## Design Decisions

**Hard gate vs. soft penalty for profit_factor < 1.0:** The profit_factor check already existed as a penalty — tightening it to a hard gate is a one-line change with clear semantics. A net-losing backtest is a disqualifier, not a downranker. The user explicitly asked for "high probability of profitability."

**Score floor at 0.0:** Zero Sharpe means zero expected edge (returns have zero mean relative to volatility). Deploying a zero-edge strategy adds transaction costs and risk with no expected return. `base > 0.0` is the correct minimum.

**Walk-forward gate changes selection, not DB persistence:** The DB stores all IS candidates for research. The env-block approval gate is the right enforcement point — it's the only output that feeds live trading parameter updates.

**"Held" threshold unchanged at 50%:** The existing `oos_score >= is_score * 0.5` threshold is already implemented and reviewed. Changing it here would be scope creep. The gate just changes what we do when no candidates meet it (fail-closed instead of silently approving).

**No new CLI flags:** Score floor and profit factor gate are not configurable — any positive-Sharpe, net-profitable strategy is the minimum bar for deployment. If a strategy can only pass with a lower bar, it shouldn't be deployed.

---

## Safety Analysis

- **Financial safety:** Changes are in the offline tuning pipeline only. No effect on order submission, position sizing, stop placement, or `evaluate_cycle()`.
- **Audit trail:** No runtime state changes; no audit events needed.
- **Intent/dispatch separation:** Not affected.
- **Pure engine boundary:** `evaluate_cycle()` untouched.
- **Paper vs. live mode:** Identical — tuning is mode-agnostic.
- **No new env vars or migrations.**

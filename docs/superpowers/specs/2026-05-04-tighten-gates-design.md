# Tighten Gate Defaults and Add Max Drawdown Gate

## Goal

Make the nightly pipeline systematically reject candidates with excessive drawdown and deploy the tighter OOS defaults the profitability gate spec already recommended.

## Problem

Two weaknesses remain after the profitability gate feature:

### 1. Nightly OOS defaults too lenient

The nightly pipeline defaults to `--min-oos-score 0.0` (accepts any positive OOS Sharpe) and `--oos-gate-ratio 0.5` (only 50% IS→OOS retention required). The profitability gate spec explicitly recommended 0.2/0.6 for production but deferred the default change for backward compatibility. That deferral is now complete — we can safely deploy tighter defaults.

### 2. No drawdown gate in IS scoring

`score_report()` disqualifies on `profit_factor < 1.0` and `score <= 0` but permits any magnitude of drawdown. A candidate with IS Sharpe 0.3 and max_drawdown_pct = 0.40 (40% equity peak-to-trough) is accepted and potentially deployed. `max_drawdown_pct` is already computed in `BacktestReport` and displayed in the top-candidates output — it just isn't enforced.

## Solution

### Change 1 — Tighten nightly OOS defaults

Change `nightly/cli.py` argument defaults:
- `--min-oos-score`: `0.0 → 0.2`
- `--oos-gate-ratio`: `0.5 → 0.6`

The `alpaca-bot-evolve` CLI keeps defaults at `0.0` / `0.5` (manual experimentation tool — the user controls strictness explicitly).

### Change 2 — Add `--max-drawdown-pct` gate to IS and OOS scoring

Add a configurable max drawdown gate to `score_report()`. When enabled (`max_drawdown_pct > 0.0`) and `report.max_drawdown_pct > max_drawdown_pct`, return `None` (disqualified). When `report.max_drawdown_pct is None` (no drawdown data), the gate is skipped — cannot disqualify on a missing metric.

**Propagation path** (all with `default=0.0` = disabled):
1. `score_report(report, *, min_trades, max_drawdown_pct=0.0)` — add one check
2. `run_sweep(..., max_drawdown_pct=0.0)` — pass through to `score_report()`
3. `run_multi_scenario_sweep(..., max_drawdown_pct=0.0)` — pass through
4. `evaluate_candidates_oos(..., max_drawdown_pct=0.0)` — pass through (OOS drawdown also gated)
5. `tuning/cli.py` — `--max-drawdown-pct FLOAT`, default `0.0`
6. `nightly/cli.py` — `--max-drawdown-pct FLOAT`, default `0.0`

**Recommended nightly invocation**: `--max-drawdown-pct 0.20` (20% drawdown limit).

## Affected Files

| File | Change |
|---|---|
| `src/alpaca_bot/tuning/sweep.py` | Add `max_drawdown_pct` param to `score_report`, `run_sweep`, `run_multi_scenario_sweep`, `evaluate_candidates_oos` |
| `src/alpaca_bot/tuning/cli.py` | Add `--max-drawdown-pct` arg; pass to sweep and OOS functions |
| `src/alpaca_bot/nightly/cli.py` | Add `--max-drawdown-pct` arg; change `--min-oos-score` default to `0.2`, `--oos-gate-ratio` default to `0.6`; pass `max_drawdown_pct` to sweep and OOS functions |
| `tests/unit/test_tuning_sweep.py` | Add 2 `score_report` drawdown gate tests |
| `tests/unit/test_tuning_sweep_cli.py` | Add test: `--max-drawdown-pct` forwarded to sweep |
| `tests/unit/test_nightly_cli.py` | Add test: tighter defaults reject marginal OOS candidate |

## What Does NOT Change

- Engine-level gates (regime, spread, news, session time, exposure, slot count, position sizing)
- Database schema — no migrations
- `Settings` — `max_drawdown_pct` is a CLI arg, not a live-trading knob
- `BacktestReport` — no schema changes
- Evolve CLI defaults for `--min-oos-score` and `--oos-gate-ratio` (stays at 0.0/0.5)
- All existing tests pass (OOS=0.4 in existing nightly tests passes new 0.2 floor; OOS/IS ratio 0.4/0.5=0.8 passes new 0.6 ratio gate)

## Safety

This is a filtering-only change. Fewer candidates pass, but no order submission, position sizing, or stop-loss logic is touched. Defaults for `max_drawdown_pct` are `0.0` (disabled) in both CLIs for backward compatibility. The nightly default changes for `min_oos_score` and `oos_gate_ratio` are safe — verified by checking that all existing nightly tests use OOS=0.4/IS=0.5 (ratio 0.8, floor 0.4 — both pass the new 0.6/0.2 defaults).

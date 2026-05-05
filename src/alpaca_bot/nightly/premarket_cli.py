from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.replay.report import BacktestReport
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.replay.splitter import split_scenario
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.sweep import (
    STRATEGY_GRIDS,
    evaluate_candidates_oos,
    run_multi_scenario_sweep,
)

# Hardcoded defaults matching Settings.from_env() field defaults.
# Used when a strategy grid key is absent from os.environ.
_PARAM_DEFAULTS: dict[str, str] = {
    "BREAKOUT_LOOKBACK_BARS": "20",
    "RELATIVE_VOLUME_THRESHOLD": "1.5",
    "DAILY_SMA_PERIOD": "20",
    "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
    "ATR_STOP_MULTIPLIER": "1.0",
    "PRIOR_DAY_HIGH_LOOKBACK_BARS": "1",
    "ORB_OPENING_BARS": "2",
    "HIGH_WATERMARK_LOOKBACK_DAYS": "252",
    "EMA_PERIOD": "9",
    "VWAP_DIP_THRESHOLD_PCT": "0.015",
    "GAP_THRESHOLD_PCT": "0.02",
    "GAP_VOLUME_THRESHOLD": "2.0",
    "BULL_FLAG_MIN_RUN_PCT": "0.02",
    "BULL_FLAG_CONSOLIDATION_RANGE_PCT": "0.5",
    "BB_PERIOD": "20",
    "BB_SQUEEZE_THRESHOLD_PCT": "0.03",
    "FAILED_BREAKDOWN_VOLUME_RATIO": "2.0",
    "FAILED_BREAKDOWN_RECAPTURE_BUFFER_PCT": "0.001",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-premarket")
    parser.add_argument("--scenario-dir", default="/data/scenarios",
                        help="Directory of *.json scenario files (default: /data/scenarios)")
    parser.add_argument("--validate-pct", type=float, default=0.2,
                        help="OOS fraction for IS/OOS split (default: 0.2)")
    parser.add_argument("--oos-gate-ratio", type=float, default=0.6,
                        help="Required OOS/IS score ratio to pass (default: 0.6)")
    parser.add_argument("--min-oos-score", type=float, default=0.2,
                        help="Minimum absolute OOS score to pass (default: 0.2)")
    parser.add_argument("--trading-mode", choices=["paper", "live"],
                        help="Override TRADING_MODE env var")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    base_env = dict(os.environ)
    if args.trading_mode:
        base_env["TRADING_MODE"] = args.trading_mode

    try:
        Settings.from_env(base_env)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    scenario_dir = Path(args.scenario_dir)
    if not scenario_dir.exists():
        print(f"Warning: --scenario-dir {scenario_dir} does not exist — "
              "nightly may not have run yet. Skipping pre-market check.")
        return 0

    files = sorted(scenario_dir.glob("*.json"))
    if len(files) < 2:
        print(f"Warning: fewer than 2 scenario files in {scenario_dir} — "
              "nightly may not have run yet. Skipping pre-market check.")
        return 0

    all_scenarios = [ReplayRunner.load_scenario(f) for f in files]
    is_scenarios = []
    oos_scenarios = []
    for s in all_scenarios:
        is_s, oos_s = split_scenario(s, in_sample_ratio=1.0 - args.validate_pct)
        is_scenarios.append(is_s)
        oos_scenarios.append(oos_s)

    now = datetime.now(timezone.utc)
    print(f"\n── Pre-market check ({now.strftime('%Y-%m-%d %H:%M UTC')}) "
          "────────────────────────────")

    # (strat_name, is_score, oos_score, report, passed, fail_reason)
    results: list[tuple[str, float | None, float | None, BacktestReport | None, bool, str]] = []

    for strat_name, strat_grid in STRATEGY_GRIDS.items():
        signal_evaluator = STRATEGY_REGISTRY[strat_name]
        constrained_grid = {
            k: [base_env.get(k, _PARAM_DEFAULTS.get(k, ""))]
            for k in strat_grid
        }

        candidates = run_multi_scenario_sweep(
            scenarios=is_scenarios,
            base_env=base_env,
            grid=constrained_grid,
            signal_evaluator=signal_evaluator,
        )
        if not candidates or candidates[0].score is None:
            results.append((strat_name, None, None, None, False, "no IS score"))
            continue

        cand = candidates[0]
        is_score = cand.score

        oos_scores_list = evaluate_candidates_oos(
            candidates=[cand],
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=3,
            signal_evaluator=signal_evaluator,
        )
        oos_score = oos_scores_list[0] if oos_scores_list else None
        report: BacktestReport | None = cand.report

        passed, fail_reason = _check_gates(
            is_score=is_score,
            oos_score=oos_score,
            report=report,
            oos_gate_ratio=args.oos_gate_ratio,
            min_oos_score=args.min_oos_score,
        )
        results.append((strat_name, is_score, oos_score, report, passed, fail_reason))

    _print_results(results)

    n_pass = sum(1 for *_, passed, _ in results if passed)
    n_total = len(results)
    print(f"Overall: {n_pass}/{n_total} strategies pass pre-market gates.")

    if n_pass == n_total:
        print("✓ All clear.")
        return 0
    else:
        n_fail = n_total - n_pass
        print(f"⚠ WARNING: {n_fail} {'strategy' if n_fail == 1 else 'strategies'} "
              "failed pre-market check — review before market open.")
        return 1


def _check_gates(
    *,
    is_score: float | None,
    oos_score: float | None,
    report: BacktestReport | None,
    oos_gate_ratio: float,
    min_oos_score: float,
) -> tuple[bool, str]:
    if oos_score is None:
        return False, "no OOS score"
    if is_score is not None and oos_score < is_score * oos_gate_ratio:
        return False, f"OOS/IS ratio {oos_score / is_score:.2f} < {oos_gate_ratio}"
    if oos_score < min_oos_score:
        return False, f"OOS {oos_score:.4f} < min {min_oos_score}"
    if report is not None:
        if report.profit_factor is not None and report.profit_factor < 1.0:
            return False, f"profit_factor {report.profit_factor:.2f} < 1.0"
        if report.sharpe_ratio is not None and report.sharpe_ratio <= 0:
            return False, f"sharpe {report.sharpe_ratio:.2f} ≤ 0"
    return True, ""


def _print_results(
    results: list[tuple[str, float | None, float | None, BacktestReport | None, bool, str]],
) -> None:
    for strat_name, is_score, oos_score, report, passed, fail_reason in results:
        is_str = f"{is_score:.4f}" if is_score is not None else "—"
        oos_str = f"{oos_score:.4f}" if oos_score is not None else "—"
        pf_str = f"{report.profit_factor:.2f}" if (report and report.profit_factor is not None) else "—"
        sharpe_str = f"{report.sharpe_ratio:.2f}" if (report and report.sharpe_ratio is not None) else "—"
        status = "✓ PASS" if passed else f"✗ FAIL  {fail_reason}"
        print(f"  {strat_name:<20s} IS={is_str}  OOS={oos_str}  "
              f"pf={pf_str}  sharpe={sharpe_str}  {status}")

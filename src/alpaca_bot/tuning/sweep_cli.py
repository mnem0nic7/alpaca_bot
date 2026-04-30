from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.sweep import DEFAULT_GRID, ParameterGrid, _parse_grid, run_sweep


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a parameter grid sweep over backfill scenario files."
    )
    parser.add_argument(
        "--scenario-dir", default="data/backfill",
        help="Directory containing *.json scenario files (default: data/backfill)",
    )
    parser.add_argument(
        "--min-trades", type=int, default=3,
        help="Minimum trades required to score a candidate (default: 3)",
    )
    parser.add_argument(
        "--strategy",
        default="breakout",
        choices=list(STRATEGY_REGISTRY),
        help="Strategy to sweep (default: breakout)",
    )
    parser.add_argument(
        "--grid", nargs="*", default=[],
        metavar="KEY=v1,v2,...",
        help="Grid overrides, e.g. BREAKOUT_LOOKBACK_BARS=15,20,25",
    )
    args = parser.parse_args()

    grid = _parse_grid(args.grid) if args.grid else DEFAULT_GRID

    scenario_dir = Path(args.scenario_dir)
    files = sorted(scenario_dir.glob("*.json"))
    if not files:
        sys.exit(f"No *.json files found in {scenario_dir}")

    base_env = dict(os.environ)

    for fpath in files:
        print(f"\n=== {fpath.name} ===")
        scenario = ReplayRunner.load_scenario(fpath)
        candidates = run_sweep(
            scenario=scenario,
            base_env=base_env,
            grid=grid,
            min_trades=args.min_trades,
            signal_evaluator=STRATEGY_REGISTRY[args.strategy],
        )
        top = [c for c in candidates if c.score is not None][:10]
        if not top:
            print("  No scored candidates (all disqualified — fewer than min_trades).")
            continue
        print(f"  {'Rank':<5} {'Score':>8}  {'Trades':>6}  {'MeanRet':>8}  Params")
        for rank, c in enumerate(top, 1):
            report = c.report
            trades = report.total_trades if report else "?"
            mean_ret = (
                f"{report.mean_return_pct:.2f}%"
                if report and report.mean_return_pct is not None
                else "n/a"
            )
            params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
            print(f"  {rank:<5} {c.score:>8.4f}  {trades:>6}  {mean_ret:>8}  {params_str}")

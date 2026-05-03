from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    ParameterGrid,
    TuningCandidate,
    run_multi_scenario_sweep,
    run_sweep,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-evolve")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", metavar="FILE",
                       help="Replay scenario (JSON or YAML)")
    group.add_argument("--scenario-dir", metavar="DIR",
                       help="Directory of *.json scenario files (multi-scenario sweep)")
    parser.add_argument("--params-grid", metavar="FILE",
                        help="Parameter grid (JSON/YAML); defaults to built-in grid")
    parser.add_argument("--output-env", metavar="FILE",
                        help="Write winning env block to FILE")
    parser.add_argument("--min-trades", type=int, default=3,
                        help="Minimum trades required to score a candidate (default: 3)")
    parser.add_argument("--strategy", default="breakout",
                        choices=list(STRATEGY_REGISTRY),
                        help="Strategy to sweep (default: breakout)")
    parser.add_argument("--aggregate", default="min", choices=["min", "mean"],
                        help="Score aggregation across scenarios: min (default) or mean")
    parser.add_argument("--no-db", action="store_true",
                        help="Skip DB persistence (just print results)")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    grid: ParameterGrid = STRATEGY_GRIDS.get(args.strategy, DEFAULT_GRID)
    if args.params_grid:
        grid = _load_grid(args.params_grid)

    signal_evaluator = STRATEGY_REGISTRY[args.strategy]
    base_env = dict(os.environ)
    now = datetime.now(timezone.utc)

    if args.scenario:
        scenario = ReplayRunner.load_scenario(args.scenario)
        total_combos = 1
        for vals in grid.values():
            total_combos *= len(vals)
        print(f"Running sweep: {total_combos} combinations over scenario '{scenario.name}'...")
        candidates = run_sweep(
            scenario=scenario,
            base_env=base_env,
            grid=grid,
            min_trades=args.min_trades,
            signal_evaluator=signal_evaluator,
        )
        scenario_name = scenario.name
    else:
        scenario_dir = Path(args.scenario_dir)
        files = sorted(scenario_dir.glob("*.json"))
        if len(files) < 2:
            sys.exit(
                f"--scenario-dir requires at least 2 *.json files; "
                f"found {len(files)} in {scenario_dir}"
            )
        scenarios = [ReplayRunner.load_scenario(f) for f in files]
        total_combos = 1
        for vals in grid.values():
            total_combos *= len(vals)
        names = ", ".join(s.name for s in scenarios)
        print(
            f"Running multi-scenario sweep: {total_combos} combinations "
            f"× {len(scenarios)} scenarios"
        )
        print(f"Scenarios: {names}")
        candidates = run_multi_scenario_sweep(
            scenarios=scenarios,
            base_env=base_env,
            grid=grid,
            min_trades_per_scenario=args.min_trades,
            aggregate=args.aggregate,
            signal_evaluator=signal_evaluator,
        )
        scenario_name = "+".join(s.name for s in scenarios)

    scored = [c for c in candidates if c.score is not None]
    unscored = [c for c in candidates if c.score is None]
    print(
        f"Scored: {len(scored)} / {len(candidates)} candidates "
        f"({len(unscored)} disqualified, min_trades={args.min_trades})"
    )

    best = scored[0] if scored else None

    _print_top_candidates(scored[:10])

    if best is None:
        print("\nNo scored candidates — increase --min-trades or provide longer scenarios.")
        return 1

    env_block = _format_env_block(best, now)
    print(f"\n{env_block}")

    if args.output_env:
        Path(args.output_env).write_text(env_block + "\n")
        print(f"Winning env block written to {args.output_env}")

    if not args.no_db:
        settings = Settings.from_env()
        _save_to_db(
            settings=settings,
            candidates=candidates,
            scenario_name=scenario_name,
            now=now,
        )

    return 0


def _load_grid(path: str) -> ParameterGrid:
    p = Path(path)
    text = p.read_text()
    if p.suffix in (".yaml", ".yml"):
        import yaml
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    return {k: [str(v) for v in vals] for k, vals in raw.items()}


def _print_top_candidates(scored: list[TuningCandidate]) -> None:
    if not scored:
        return
    print("\nTop candidates:")
    for i, c in enumerate(scored, 1):
        report = c.report
        trades = report.total_trades if report else 0
        win = f"{report.win_rate:.0%}" if (report and report.win_rate is not None) else "—"
        sharpe = f"{c.score:.4f}" if c.score is not None else "—"
        pf = f"{report.profit_factor:.2f}" if (report and report.profit_factor is not None) else "—"
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  pf={pf:>5s}  {params_str}")


def _format_env_block(best: TuningCandidate, now: datetime) -> str:
    report = best.report
    trades = report.total_trades if report else 0
    win = f"{report.win_rate:.0%}" if (report and report.win_rate is not None) else "—"
    score_str = f"{best.score:.4f}" if best.score is not None else "—"
    lines = [
        f"# Best params from tuning run {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"# Score={score_str}  Trades={trades}  WinRate={win}",
    ]
    lines += [f"{k}={v}" for k, v in best.params.items()]
    return "\n".join(lines)


def _save_to_db(
    *,
    settings: Settings,
    candidates: list[TuningCandidate],
    scenario_name: str,
    now: datetime,
) -> None:
    from alpaca_bot.storage.db import connect_postgres
    from alpaca_bot.storage.repositories import TuningResultStore
    conn = connect_postgres(settings.database_url)
    try:
        store = TuningResultStore(conn)
        run_id = store.save_run(
            scenario_name=scenario_name,
            trading_mode=settings.trading_mode.value,
            candidates=candidates,
            created_at=now,
        )
        print(f"Results saved to DB (run_id={run_id})")
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

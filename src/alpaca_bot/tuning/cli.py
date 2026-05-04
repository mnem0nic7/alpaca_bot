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
from alpaca_bot.replay.splitter import split_scenario
from alpaca_bot.tuning.sweep import (
    DEFAULT_GRID,
    STRATEGY_GRIDS,
    ParameterGrid,
    TuningCandidate,
    evaluate_candidates_oos,
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
    parser.add_argument("--validate-pct", type=float, default=0.0,
                        help="Fraction of each scenario held out for OOS validation (0 = disabled)")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    validate_pct: float = args.validate_pct
    if validate_pct != 0.0:
        if not (0.0 < validate_pct < 1.0):
            sys.exit(f"--validate-pct must be in (0.0, 1.0), got {validate_pct}")
        if args.scenario:
            sys.exit("--validate-pct requires --scenario-dir; single-scenario walk-forward is not supported")

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
        all_scenarios = [ReplayRunner.load_scenario(f) for f in files]
        total_combos = 1
        for vals in grid.values():
            total_combos *= len(vals)

        if validate_pct > 0.0:
            is_scenarios = []
            oos_scenarios = []
            for s in all_scenarios:
                is_s, oos_s = split_scenario(s, in_sample_ratio=1.0 - validate_pct)
                is_scenarios.append(is_s)
                oos_scenarios.append(oos_s)
            sweep_scenarios = is_scenarios
            oos_pct_int = round(validate_pct * 100)
            print(
                f"Walk-forward mode: IS={100 - oos_pct_int}% / OOS={oos_pct_int}% "
                f"of each scenario"
            )
        else:
            sweep_scenarios = all_scenarios
            oos_scenarios = []

        names = ", ".join(s.name for s in all_scenarios)
        print(
            f"Running multi-scenario sweep: {total_combos} combinations "
            f"× {len(sweep_scenarios)} scenarios"
        )
        print(f"Scenarios: {names}")
        candidates = run_multi_scenario_sweep(
            scenarios=sweep_scenarios,
            base_env=base_env,
            grid=grid,
            min_trades_per_scenario=args.min_trades,
            aggregate=args.aggregate,
            signal_evaluator=signal_evaluator,
        )
        scenario_name = "+".join(s.name for s in all_scenarios)

    scored = [c for c in candidates if c.score is not None]
    unscored = [c for c in candidates if c.score is None]
    print(
        f"Scored: {len(scored)} / {len(candidates)} candidates "
        f"({len(unscored)} disqualified, min_trades={args.min_trades})"
    )

    best = scored[0] if scored else None

    _print_top_candidates(scored[:10])

    if validate_pct > 0.0 and oos_scenarios:
        top10 = scored[:10]
        oos_scores = evaluate_candidates_oos(
            candidates=top10,
            oos_scenarios=oos_scenarios,
            base_env=base_env,
            min_trades=args.min_trades,
            aggregate=args.aggregate,
            signal_evaluator=signal_evaluator,
        )
        _print_walk_forward_block(top10, oos_scores, validate_pct=validate_pct, aggregate=args.aggregate)
        held_pairs = [
            (c, s) for c, s in zip(top10, oos_scores)
            if s is not None and c.score is not None and s >= c.score * 0.5
        ]
        if not held_pairs:
            print("\nNo walk-forward held candidates — approval gate blocked all.")
            return 1
        best = max(held_pairs, key=lambda pair: pair[1])[0]

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
        stop_total = (report.stop_wins + report.stop_losses) if report else 0
        stop_pct = f"{stop_total / trades:.0%}" if trades > 0 else "—"
        max_cl = report.max_consecutive_losses if report else 0
        if (report and report.avg_win_return_pct is not None
                and report.avg_loss_return_pct is not None
                and report.avg_loss_return_pct != 0.0):
            r_multiple = report.avg_win_return_pct / abs(report.avg_loss_return_pct)
            r_str = f"{r_multiple:.2f}"
        else:
            r_str = "—"
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:2d}] score={sharpe:>8s}  trades={trades:2d}  win={win:>5s}  pf={pf:>5s}  R={r_str:>5s}  stop%={stop_pct:>4s}  maxcl={max_cl:>2d}  {params_str}")


def _print_walk_forward_block(
    candidates: list[TuningCandidate],
    oos_scores: list[float | None],
    *,
    validate_pct: float,
    aggregate: str,
    oos_gate_ratio: float = 0.5,
    min_oos_score: float = 0.0,
) -> None:
    oos_pct_int = round(validate_pct * 100)
    ratio_pct = round(oos_gate_ratio * 100)
    floor_str = f"{min_oos_score:.2f}" if min_oos_score > 0.0 else "none"
    print(f"\nWalk-forward validation (OOS: {oos_pct_int}% of each scenario, aggregate={aggregate})")
    print(f"  IS score threshold for \"held\": OOS ≥ IS × {ratio_pct}%  AND  OOS ≥ {floor_str}")
    print()
    print(f"  {'[Rank]':>6}  {'IS-score':>8}  {'OOS-score':>9}  {'OOS-trades':>10}  {'held?':>5}  Params")
    for i, (c, oos_score) in enumerate(zip(candidates, oos_scores), 1):
        is_score_str = f"{c.score:.4f}" if c.score is not None else "    None"
        oos_score_str = f"{oos_score:.4f}" if oos_score is not None else "    None"
        held = (
            "✓"
            if (
                oos_score is not None
                and c.score is not None
                and oos_score >= c.score * oos_gate_ratio
                and oos_score >= min_oos_score
            )
            else "✗"
        )
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"  [{i:3d}]  {is_score_str:>8}  {oos_score_str:>9}  {'—':>10}  {held:>5}  {params_str}")


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

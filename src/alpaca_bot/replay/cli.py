from __future__ import annotations

import argparse
import csv
import dataclasses
from datetime import date
import hashlib
import io
import json
import math
import os
import sys
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.replay.audit import StrategyAuditRow, run_audit
from alpaca_bot.replay.break_even import (
    DEFAULT_SLIPPAGE_LADDER,
    format_break_even_markdown,
    run_break_even_sweep,
)
from alpaca_bot.replay.lever_sweep import (
    build_coarse_grid,
    build_ofat_grid,
    format_lever_sweep_markdown,
    run_lever_sweep,
)
from alpaca_bot.replay.portfolio import portfolio_pooled_trades
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY
from alpaca_bot.tuning.sweep import DEFAULT_GRID, _parse_grid, run_sweep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-backtest")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # --- run subcommand ---
    run_p = subparsers.add_parser("run", help="Single strategy against one scenario")
    run_p.add_argument("--scenario", required=True, metavar="FILE")
    run_p.add_argument("--output", metavar="FILE", default="-")
    run_p.add_argument("--format", choices=["json", "csv"], default="json")
    run_p.add_argument(
        "--strategy",
        choices=list(STRATEGY_REGISTRY),
        default=None,
        help="strategy to backtest (default: breakout)",
    )

    # --- compare subcommand ---
    cmp_p = subparsers.add_parser(
        "compare", help="All (or selected) strategies against one scenario"
    )
    cmp_p.add_argument("--scenario", required=True, metavar="FILE")
    cmp_p.add_argument(
        "--strategies",
        default=None,
        metavar="s1,s2,...",
        help="comma-separated strategy names (default: all registered)",
    )
    cmp_p.add_argument("--format", choices=["json", "csv"], default="json")
    cmp_p.add_argument("--output", metavar="FILE", default="-")

    # --- sweep subcommand ---
    swp_p = subparsers.add_parser(
        "sweep", help="Parameter grid sweep of one strategy against one scenario"
    )
    swp_p.add_argument("--scenario", required=True, metavar="FILE")
    swp_p.add_argument(
        "--strategy",
        choices=list(STRATEGY_REGISTRY),
        required=True,
        help="strategy to sweep",
    )
    swp_p.add_argument(
        "--grid",
        nargs="*",
        default=[],
        metavar="KEY=v1,v2,...",
        help="parameter overrides (default: DEFAULT_GRID)",
    )
    swp_p.add_argument("--min-trades", type=int, default=3, metavar="N")

    # --- audit subcommand ---
    aud_p = subparsers.add_parser(
        "audit",
        help="Cost-aware significance audit of strategies across a scenario directory",
    )
    aud_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    aud_p.add_argument(
        "--strategies",
        default=None,
        metavar="s1,s2,...",
        help="comma-separated strategy names (default: all registered)",
    )
    aud_p.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        metavar="BPS",
        help="cost level for the costed run (default: REPLAY_SLIPPAGE_BPS)",
    )
    aud_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="audit only the first N scenario files (0 = all)",
    )
    aud_p.add_argument(
        "--sample-size", type=int, default=0, metavar="N",
        help="deterministically sample N scenario files across the directory (0 = disabled)",
    )
    aud_p.add_argument(
        "--sample-seed", default="0", metavar="SEED",
        help="seed for --sample-size scenario selection (default: 0)",
    )
    aud_p.add_argument("--output", metavar="FILE", default="-")
    aud_p.add_argument("--json", dest="json_path", metavar="FILE", default=None)

    # --- lever-sweep subcommand ---
    lev_p = subparsers.add_parser(
        "lever-sweep",
        help="Sweep cost-drag/selectivity levers; rank by after-cost ci_low",
    )
    lev_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    lev_p.add_argument(
        "--strategy", choices=list(STRATEGY_REGISTRY), required=True,
        help="strategy to sweep (bull_flag / vwap_reversion are the leads)",
    )
    lev_p.add_argument(
        "--slippage-bps", type=float, default=None, metavar="BPS",
        help="cost level (default: REPLAY_SLIPPAGE_BPS)",
    )
    lev_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="use only the first N scenario files (0 = all)",
    )
    lev_p.add_argument(
        "--sample-size", type=int, default=0, metavar="N",
        help="deterministically sample N scenario files across the directory (0 = disabled)",
    )
    lev_p.add_argument(
        "--sample-seed", default="0", metavar="SEED",
        help="seed for --sample-size scenario selection (default: 0)",
    )
    lev_p.add_argument(
        "--coarse", action="store_true",
        help="reduced grid (one value per family) for a fast pass",
    )
    lev_p.add_argument(
        "--no-walk-forward", dest="walk_forward", action="store_false",
        help="skip the IS/OOS split (audit the full scenarios in-sample only)",
    )
    lev_p.add_argument("--top-k", type=int, default=5, metavar="K")
    lev_p.add_argument("--output", metavar="FILE", default="-")

    # --- break-even subcommand ---
    be_p = subparsers.add_parser(
        "break-even",
        help="Slippage ladder: find where after-cost ci_low crosses zero",
    )
    be_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    be_p.add_argument(
        "--strategy",
        action="append",
        choices=list(STRATEGY_REGISTRY),
        metavar="NAME",
        help="strategy to score (repeatable; default: bull_flag, vwap_reversion)",
    )
    be_p.add_argument(
        "--slippage-ladder",
        default=None,
        metavar="b1,b2,...",
        help="comma-separated bps/side levels (default: 0,1,2,3,4,5)",
    )
    be_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="use only the first N scenario files (0 = all)",
    )
    be_p.add_argument(
        "--sample-size", type=int, default=0, metavar="N",
        help="deterministically sample N scenario files across the directory (0 = disabled)",
    )
    be_p.add_argument(
        "--sample-seed", default="0", metavar="SEED",
        help="seed for --sample-size scenario selection (default: 0)",
    )
    be_p.add_argument("--output", metavar="FILE", default="-")

    # --- portfolio-audit subcommand ---
    port_p = subparsers.add_parser(
        "portfolio-audit",
        help="Cross-sectional top-K replay: pool symbols into one equity pool, "
        "sweep max_open_positions (K); read-only diagnostic",
    )
    port_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    port_p.add_argument(
        "--strategy",
        action="append",
        choices=list(STRATEGY_REGISTRY),
        required=True,
        metavar="NAME",
        help="strategy to score (repeatable)",
    )
    port_p.add_argument(
        "--slippage-bps", type=float, default=5.0, metavar="BPS",
        help="cost level for the costed run (default: 5.0)",
    )
    port_p.add_argument(
        "--max-open-positions",
        action="append",
        type=int,
        default=None,
        metavar="K",
        help="portfolio top-K cap (repeatable; default: settings.max_open_positions)",
    )
    port_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="use only the first N scenario files (0 = all)",
    )
    port_p.add_argument(
        "--sample-size", type=int, default=0, metavar="N",
        help="deterministically sample N scenario files across the directory (0 = disabled)",
    )
    port_p.add_argument(
        "--sample-seed", default="0", metavar="SEED",
        help="seed for --sample-size scenario selection (default: 0)",
    )
    port_p.add_argument(
        "--starting-equity",
        type=float,
        default=None,
        metavar="DOLLARS",
        help=(
            "override every scenario's starting equity, useful for matching "
            "live confidence-scaled paper sizing"
        ),
    )
    port_p.add_argument("--output", metavar="FILE", default="-")
    port_p.add_argument(
        "--jsonl",
        dest="jsonl_path",
        metavar="FILE",
        default=None,
        help="write one JSON line per completed K block, flushed during long runs",
    )

    # --- proof-horizon subcommand ---
    horizon_p = subparsers.add_parser(
        "proof-horizon",
        help=(
            "Replay a portfolio once, then measure how quickly each historical "
            "start date reaches the live proof gate"
        ),
    )
    horizon_p.add_argument("--scenario-dir", required=True, metavar="DIR")
    horizon_p.add_argument(
        "--strategy",
        choices=list(STRATEGY_REGISTRY),
        required=True,
        metavar="NAME",
        help="strategy to replay",
    )
    horizon_p.add_argument(
        "--slippage-bps",
        type=float,
        default=None,
        metavar="BPS",
        help="cost level for replay fills (default: REPLAY_SLIPPAGE_BPS)",
    )
    horizon_p.add_argument(
        "--max-open-positions",
        type=int,
        default=None,
        metavar="K",
        help="portfolio top-K cap (default: settings.max_open_positions)",
    )
    horizon_p.add_argument(
        "--starting-equity",
        type=float,
        default=None,
        metavar="DOLLARS",
        help=(
            "override every scenario's starting equity, useful for matching "
            "live confidence-scaled paper sizing"
        ),
    )
    horizon_p.add_argument(
        "--min-trades",
        type=int,
        default=10,
        metavar="N",
        help="closed-trade threshold for proof pass (default: 10)",
    )
    horizon_p.add_argument(
        "--min-pnl",
        type=float,
        default=0.01,
        metavar="DOLLARS",
        help="cumulative P&L threshold for proof pass (default: 0.01)",
    )
    horizon_p.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="use only the first N scenario files (0 = all)",
    )
    horizon_p.add_argument(
        "--sample-size", type=int, default=0, metavar="N",
        help="deterministically sample N scenario files across the directory (0 = disabled)",
    )
    horizon_p.add_argument(
        "--sample-seed", default="0", metavar="SEED",
        help="seed for --sample-size scenario selection (default: 0)",
    )
    horizon_p.add_argument("--output", metavar="FILE", default="-")
    horizon_p.add_argument("--json", dest="json_path", metavar="FILE", default=None)

    args = parser.parse_args(argv)

    if args.subcommand == "run":
        return _cmd_run(args)
    if args.subcommand == "compare":
        return _cmd_compare(args)
    if args.subcommand == "sweep":
        return _cmd_sweep(args)
    if args.subcommand == "audit":
        return _cmd_audit(args)
    if args.subcommand == "lever-sweep":
        return _cmd_lever_sweep(args)
    if args.subcommand == "break-even":
        return _cmd_break_even(args)
    if args.subcommand == "portfolio-audit":
        return _cmd_portfolio_audit(args)
    if args.subcommand == "proof-horizon":
        return _cmd_proof_horizon(args)
    return 1  # unreachable — argparse enforces subcommand


def _cmd_run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    strategy_name = args.strategy or "breakout"
    signal_evaluator = STRATEGY_REGISTRY[args.strategy] if args.strategy else None
    runner = ReplayRunner(settings, signal_evaluator=signal_evaluator, strategy_name=strategy_name)
    scenario = runner.load_scenario(args.scenario)
    result = runner.run(scenario)
    report: BacktestReport = result.backtest_report  # type: ignore[assignment]
    out_text = _format_report(report, args.format)
    _write_output(out_text, args.output)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",")]
        invalid = [n for n in names if n not in STRATEGY_REGISTRY]
        if invalid:
            print(f"Unknown strategies: {', '.join(invalid)}", file=sys.stderr)
            sys.exit(1)
    else:
        names = list(STRATEGY_REGISTRY)

    # Load scenario once; reuse across all strategy runners (frozen dataclass — no state)
    first_runner = ReplayRunner(settings, strategy_name=names[0])
    scenario = first_runner.load_scenario(args.scenario)

    reports: list[BacktestReport] = []
    for name in names:
        evaluator = STRATEGY_REGISTRY[name]
        runner = ReplayRunner(settings, signal_evaluator=evaluator, strategy_name=name)
        result = runner.run(scenario)
        reports.append(result.backtest_report)  # type: ignore[arg-type]

    if args.format == "json":
        out_text = _format_compare_json(reports)
    else:
        out_text = _format_compare_csv(reports)

    _write_output(out_text, args.output)
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    signal_evaluator = STRATEGY_REGISTRY[args.strategy]

    runner = ReplayRunner(settings, strategy_name=args.strategy)
    scenario = runner.load_scenario(args.scenario)

    grid = _parse_grid(args.grid) if args.grid else DEFAULT_GRID

    candidates = run_sweep(
        scenario=scenario,
        base_env=dict(os.environ),
        grid=grid,
        min_trades=args.min_trades,
        signal_evaluator=signal_evaluator,
    )

    top = [c for c in candidates if c.score is not None][:10]
    if not top:
        print("No scored candidates (all disqualified — fewer than min-trades).")
        return 0

    print(f"{'Rank':<5} {'Score':>8}  {'Trades':>6}  {'MeanRet':>8}  Params")
    for rank, c in enumerate(top, 1):
        report = c.report
        trades = report.total_trades if report else "?"
        mean_ret = (
            f"{report.mean_return_pct:.2f}%"
            if report and report.mean_return_pct is not None
            else "n/a"
        )
        params_str = " ".join(f"{k}={v}" for k, v in c.params.items())
        print(f"{rank:<5} {c.score:>8.4f}  {trades:>6}  {mean_ret:>8}  {params_str}")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    if args.strategies:
        names = [s.strip() for s in args.strategies.split(",")]
        invalid = [n for n in names if n not in STRATEGY_REGISTRY]
        if invalid:
            print(f"Unknown strategies: {', '.join(invalid)}", file=sys.stderr)
            return 1
    else:
        names = list(STRATEGY_REGISTRY)

    try:
        paths = _select_scenario_paths(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not paths:
        print(f"No scenario files in {args.scenario_dir}", file=sys.stderr)
        return 1

    scenarios = [ReplayRunner.load_scenario(p) for p in paths]
    bps = (
        args.slippage_bps
        if args.slippage_bps is not None
        else settings.replay_slippage_bps
    )

    rows = run_audit(
        scenarios=scenarios,
        settings=settings,
        strategies=names,
        slippage_bps=bps,
        on_progress=lambda msg: print(f"[audit] {msg}", file=sys.stderr),
    )

    _write_output(_format_audit_markdown(rows, slippage_bps=bps), args.output)
    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps([dataclasses.asdict(r) for r in rows], indent=2)
        )
    return 0


def _cmd_lever_sweep(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    try:
        paths = _select_scenario_paths(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not paths:
        print(f"No scenario files in {args.scenario_dir}", file=sys.stderr)
        return 1
    scenarios = [ReplayRunner.load_scenario(p) for p in paths]

    bps = (
        args.slippage_bps
        if args.slippage_bps is not None
        else settings.replay_slippage_bps
    )
    grid = (
        build_coarse_grid(settings) if args.coarse else build_ofat_grid(settings)
    )

    rows = run_lever_sweep(
        scenarios=scenarios,
        base_settings=settings,
        strategy=args.strategy,
        grid=grid,
        slippage_bps=bps,
        walk_forward=args.walk_forward,
        top_k=args.top_k,
        on_progress=lambda msg: print(f"[lever-sweep] {msg}", file=sys.stderr),
    )

    _write_output(
        format_lever_sweep_markdown(rows, strategy=args.strategy, slippage_bps=bps),
        args.output,
    )
    return 0


def _cmd_break_even(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    try:
        paths = _select_scenario_paths(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not paths:
        print(f"No scenario files in {args.scenario_dir}", file=sys.stderr)
        return 1
    scenarios = [ReplayRunner.load_scenario(p) for p in paths]

    strategies = args.strategy or ["bull_flag", "vwap_reversion"]
    if args.slippage_ladder is not None:
        ladder = tuple(float(x) for x in args.slippage_ladder.split(","))
    else:
        ladder = DEFAULT_SLIPPAGE_LADDER

    results = [
        run_break_even_sweep(
            scenarios=scenarios,
            settings=settings,
            strategy=name,
            slippage_ladder=ladder,
            on_progress=lambda msg: print(f"[break-even] {msg}", file=sys.stderr),
        )
        for name in strategies
    ]

    _write_output(format_break_even_markdown(results), args.output)
    return 0


def _cmd_portfolio_audit(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    try:
        paths = _select_scenario_paths(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not paths:
        print(f"No scenario files in {args.scenario_dir}", file=sys.stderr)
        return 1
    scenarios = [ReplayRunner.load_scenario(p) for p in paths]
    if args.starting_equity is not None:
        if args.starting_equity <= 0.0:
            print("--starting-equity must be greater than 0", file=sys.stderr)
            return 1
        scenarios = [
            dataclasses.replace(scenario, starting_equity=args.starting_equity)
            for scenario in scenarios
        ]
    duplicate_symbols = _duplicate_scenario_symbols(scenarios)
    if duplicate_symbols:
        print(
            "portfolio-audit requires one scenario per symbol; duplicate "
            f"scenario symbols: {', '.join(duplicate_symbols)}",
            file=sys.stderr,
        )
        return 1

    bps = args.slippage_bps
    ks = args.max_open_positions or [settings.max_open_positions]
    jsonl_path = Path(args.jsonl_path) if args.jsonl_path else None
    if jsonl_path is not None:
        jsonl_path.write_text("")

    blocks = [
        f"# Cross-sectional top-K portfolio audit — {bps:g} bps/side",
        "",
        f"Scenarios pooled into one equity pool: {len(scenarios)}. "
        "Read-only diagnostic — no production config change.",
        "",
    ]
    if args.starting_equity is not None:
        blocks.extend([
            f"Scenario starting equity override: ${args.starting_equity:,.2f}.",
            "",
        ])
    def emit_progress(msg: str) -> None:
        print(f"[portfolio-audit] {msg}", file=sys.stderr)

    def portfolio_pooled_trades_with_progress(scenarios, settings, strategy_name):
        return portfolio_pooled_trades(
            scenarios,
            settings,
            strategy_name,
            on_progress=emit_progress,
        )

    for k in ks:
        ksettings = dataclasses.replace(settings, max_open_positions=k)
        rows = run_audit(
            scenarios=scenarios,
            settings=ksettings,
            strategies=args.strategy,
            slippage_bps=bps,
            pooled_trades_fn=portfolio_pooled_trades_with_progress,
            on_progress=emit_progress,
        )
        blocks.append(f"## K={k} (max_open_positions)")
        blocks.append("")
        blocks.append(_format_audit_markdown(rows, slippage_bps=bps))
        if jsonl_path is not None:
            _append_portfolio_audit_jsonl(
                jsonl_path,
                max_open_positions=k,
                slippage_bps=bps,
                scenarios=len(scenarios),
                rows=rows,
            )

    _write_output("\n".join(blocks), args.output)
    return 0


@dataclasses.dataclass(frozen=True)
class ProofHorizonSummary:
    strategy: str
    scenarios: int
    sessions: int
    trades: int
    total_pnl: float
    slippage_bps: float
    max_open_positions: int
    starting_equity: float | None
    min_trades: int
    min_pnl: float
    historical_starts_checked: int
    starts_eventually_passed: int
    starts_not_proven_by_data_end: int
    eventual_pass_rate: float | None
    starts_reaching_min_trades: int
    first_threshold_passes: int
    first_threshold_pass_rate: float | None
    first_threshold_failures_later_recovered: int
    median_sessions_to_pass: int | None
    p90_sessions_to_pass: int | None
    p95_sessions_to_pass: int | None
    slowest_sessions_to_pass: int | None
    active_trade_days: int
    last_sessions: list[str]


def _cmd_proof_horizon(args: argparse.Namespace) -> int:
    settings = Settings.from_env()

    if args.min_trades <= 0:
        print("--min-trades must be greater than 0", file=sys.stderr)
        return 1
    if args.max_open_positions is not None and args.max_open_positions <= 0:
        print("--max-open-positions must be greater than 0", file=sys.stderr)
        return 1
    if args.starting_equity is not None and args.starting_equity <= 0.0:
        print("--starting-equity must be greater than 0", file=sys.stderr)
        return 1
    if args.slippage_bps is not None and args.slippage_bps < 0.0:
        print("--slippage-bps must be non-negative", file=sys.stderr)
        return 1

    try:
        paths = _select_scenario_paths(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not paths:
        print(f"No scenario files in {args.scenario_dir}", file=sys.stderr)
        return 1
    scenarios = [ReplayRunner.load_scenario(p) for p in paths]
    if args.starting_equity is not None:
        scenarios = [
            dataclasses.replace(scenario, starting_equity=args.starting_equity)
            for scenario in scenarios
        ]
    duplicate_symbols = _duplicate_scenario_symbols(scenarios)
    if duplicate_symbols:
        print(
            "proof-horizon requires one scenario per symbol; duplicate "
            f"scenario symbols: {', '.join(duplicate_symbols)}",
            file=sys.stderr,
        )
        return 1

    slippage_bps = (
        float(args.slippage_bps)
        if args.slippage_bps is not None
        else float(settings.replay_slippage_bps)
    )
    max_open_positions = int(args.max_open_positions or settings.max_open_positions)
    replay_settings = dataclasses.replace(
        settings,
        max_open_positions=max_open_positions,
        replay_slippage_bps=slippage_bps,
    )

    def emit_progress(msg: str) -> None:
        print(f"[proof-horizon] {msg}", file=sys.stderr)

    trades = portfolio_pooled_trades(
        scenarios,
        replay_settings,
        args.strategy,
        on_progress=emit_progress,
    )
    summary = _proof_horizon_summary(
        scenarios=scenarios,
        trades=trades,
        settings=replay_settings,
        strategy=args.strategy,
        slippage_bps=slippage_bps,
        max_open_positions=max_open_positions,
        starting_equity=args.starting_equity,
        min_trades=args.min_trades,
        min_pnl=args.min_pnl,
    )
    _write_output(_format_proof_horizon_markdown(summary), args.output)
    if args.json_path is not None:
        Path(args.json_path).write_text(json.dumps(dataclasses.asdict(summary)) + "\n")
    return 0


def _proof_horizon_summary(
    *,
    scenarios: list,
    trades: list[ReplayTradeRecord],
    settings: Settings,
    strategy: str,
    slippage_bps: float,
    max_open_positions: int,
    starting_equity: float | None,
    min_trades: int,
    min_pnl: float,
) -> ProofHorizonSummary:
    sessions = sorted({
        bar.timestamp.astimezone(settings.market_timezone).date()
        for scenario in scenarios
        for bar in scenario.intraday_bars
    })
    pnls_by_exit_session: dict[date, list[float]] = {}
    for trade in trades:
        exit_session = trade.exit_time.astimezone(settings.market_timezone).date()
        pnls_by_exit_session.setdefault(exit_session, []).append(float(trade.pnl))

    starts_eventually_passed = 0
    starts_not_proven = 0
    starts_reaching_min_trades = 0
    first_threshold_passes = 0
    first_threshold_failures_later_recovered = 0
    sessions_to_pass: list[int] = []

    for start_index, _start_session in enumerate(sessions):
        trade_count = 0
        pnl = 0.0
        first_threshold_seen = False
        first_threshold_passed = False
        pass_index: int | None = None

        for session_index in range(start_index, len(sessions)):
            session = sessions[session_index]
            session_pnls = pnls_by_exit_session.get(session, [])
            if session_pnls:
                trade_count += len(session_pnls)
                pnl += sum(session_pnls)
            if not first_threshold_seen and trade_count >= min_trades:
                first_threshold_seen = True
                first_threshold_passed = pnl >= min_pnl
                starts_reaching_min_trades += 1
                if first_threshold_passed:
                    first_threshold_passes += 1
            if trade_count >= min_trades and pnl >= min_pnl:
                pass_index = session_index
                break

        if pass_index is None:
            starts_not_proven += 1
        else:
            starts_eventually_passed += 1
            sessions_to_pass.append(pass_index - start_index + 1)
            if first_threshold_seen and not first_threshold_passed:
                first_threshold_failures_later_recovered += 1

    sessions_to_pass.sort()
    historical_starts = len(sessions)
    eventual_pass_rate = (
        starts_eventually_passed / historical_starts
        if historical_starts > 0
        else None
    )
    first_threshold_pass_rate = (
        first_threshold_passes / starts_reaching_min_trades
        if starts_reaching_min_trades > 0
        else None
    )
    active_trade_days = sum(1 for pnls in pnls_by_exit_session.values() if pnls)

    return ProofHorizonSummary(
        strategy=strategy,
        scenarios=len(scenarios),
        sessions=historical_starts,
        trades=len(trades),
        total_pnl=round(sum(float(trade.pnl) for trade in trades), 2),
        slippage_bps=slippage_bps,
        max_open_positions=max_open_positions,
        starting_equity=starting_equity,
        min_trades=min_trades,
        min_pnl=min_pnl,
        historical_starts_checked=historical_starts,
        starts_eventually_passed=starts_eventually_passed,
        starts_not_proven_by_data_end=starts_not_proven,
        eventual_pass_rate=eventual_pass_rate,
        starts_reaching_min_trades=starts_reaching_min_trades,
        first_threshold_passes=first_threshold_passes,
        first_threshold_pass_rate=first_threshold_pass_rate,
        first_threshold_failures_later_recovered=(
            first_threshold_failures_later_recovered
        ),
        median_sessions_to_pass=_ceil_percentile(sessions_to_pass, 0.50),
        p90_sessions_to_pass=_ceil_percentile(sessions_to_pass, 0.90),
        p95_sessions_to_pass=_ceil_percentile(sessions_to_pass, 0.95),
        slowest_sessions_to_pass=max(sessions_to_pass) if sessions_to_pass else None,
        active_trade_days=active_trade_days,
        last_sessions=[session.isoformat() for session in sessions[-5:]],
    )


def _ceil_percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    index = int(math.ceil(len(values) * pct)) - 1
    index = max(0, min(len(values) - 1, index))
    return values[index]


def _format_proof_horizon_markdown(summary: ProofHorizonSummary) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2%}"

    def maybe_int(value: int | None) -> str:
        return "n/a" if value is None else str(value)

    lines = [
        f"# Proof horizon audit - {summary.strategy}",
        "",
        "Read-only diagnostic - no production config change.",
        "",
        f"- scenarios: `{summary.scenarios}`",
        f"- sessions: `{summary.sessions}`",
        f"- trades: `{summary.trades}`",
        f"- total P&L: `${summary.total_pnl:.2f}`",
        f"- slippage: `{summary.slippage_bps:g}` bps/side",
        f"- max open positions: `{summary.max_open_positions}`",
    ]
    if summary.starting_equity is not None:
        lines.append(f"- starting equity override: `${summary.starting_equity:,.2f}`")
    lines.extend([
        f"- proof gate: `{summary.min_trades}` closed trades and "
        f"`${summary.min_pnl:.2f}` cumulative P&L",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| historical starts checked | {summary.historical_starts_checked} |",
        f"| starts that eventually reached proof gate | {summary.starts_eventually_passed} |",
        f"| starts not proven by data end | {summary.starts_not_proven_by_data_end} |",
        f"| eventual pass rate | {pct(summary.eventual_pass_rate)} |",
        f"| starts reaching trade threshold | {summary.starts_reaching_min_trades} |",
        f"| first-threshold pass rate | {pct(summary.first_threshold_pass_rate)} |",
        f"| first-threshold failures that later recovered | "
        f"{summary.first_threshold_failures_later_recovered} |",
        f"| median sessions to proof pass | {maybe_int(summary.median_sessions_to_pass)} |",
        f"| p90 sessions to proof pass | {maybe_int(summary.p90_sessions_to_pass)} |",
        f"| p95 sessions to proof pass | {maybe_int(summary.p95_sessions_to_pass)} |",
        f"| slowest observed pass | {maybe_int(summary.slowest_sessions_to_pass)} |",
        f"| active trade days | {summary.active_trade_days} |",
        f"| final sessions in sample | {', '.join(summary.last_sessions) or 'none'} |",
        "",
    ])
    return "\n".join(lines)


def _select_scenario_paths(args: argparse.Namespace) -> list[Path]:
    paths = sorted(Path(args.scenario_dir).glob("*.json"))
    limit = int(getattr(args, "limit", 0) or 0)
    sample_size = int(getattr(args, "sample_size", 0) or 0)
    if limit < 0:
        raise ValueError("--limit must be a non-negative integer")
    if sample_size < 0:
        raise ValueError("--sample-size must be a non-negative integer")
    if limit > 0 and sample_size > 0:
        raise ValueError("--limit and --sample-size cannot be combined")
    if sample_size > 0 and sample_size < len(paths):
        seed = str(getattr(args, "sample_seed", "0"))
        paths = sorted(
            sorted(paths, key=lambda path: _scenario_sample_key(path, seed))[
                :sample_size
            ]
        )
    elif limit > 0:
        paths = paths[:limit]
    return paths


def _scenario_sample_key(path: Path, seed: str) -> str:
    return hashlib.sha256(f"{seed}:{path.name}".encode("utf-8")).hexdigest()


def _duplicate_scenario_symbols(scenarios: list) -> list[str]:
    counts: dict[str, int] = {}
    for scenario in scenarios:
        counts[scenario.symbol] = counts.get(scenario.symbol, 0) + 1
    return sorted(symbol for symbol, count in counts.items() if count > 1)


def _append_portfolio_audit_jsonl(
    path: Path,
    *,
    max_open_positions: int,
    slippage_bps: float,
    scenarios: int,
    rows: list[StrategyAuditRow],
) -> None:
    payload = {
        "max_open_positions": max_open_positions,
        "slippage_bps": slippage_bps,
        "scenarios": scenarios,
        "rows": [dataclasses.asdict(r) for r in rows],
    }
    with path.open("a") as f:
        f.write(json.dumps(payload) + "\n")
        f.flush()


def _format_audit_markdown(rows: list[StrategyAuditRow], *, slippage_bps: float) -> str:
    def fmt(v: float | None, spec: str = ".2f") -> str:
        return "n/a" if v is None else format(v, spec)

    lines = [
        f"# Strategy audit — {slippage_bps:g} bps/side vs frictionless",
        "",
        "| strategy | scenarios | trades | win rate | profit factor | total P&L "
        "| mean/trade | ann. Sharpe | 95% CI mean/trade | p(mean<=0) "
        "| frictionless P&L | cost drag | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ci = (
            f"[{fmt(r.ci_low, '.4f')}, {fmt(r.ci_high, '.4f')}]"
            if r.ci_low is not None
            else "n/a"
        )
        lines.append(
            f"| {r.strategy} | {r.scenarios} | {r.trades} "
            f"| {fmt(r.win_rate, '.1%')} | {fmt(r.profit_factor)} "
            f"| {r.total_pnl:.2f} | {fmt(r.mean_trade_pnl, '.4f')} "
            f"| {fmt(r.annualized_sharpe)} | {ci} | {fmt(r.p_positive, '.4f')} "
            f"| {r.zero_cost_total_pnl:.2f} | {r.cost_drag:.2f} | **{r.verdict}** |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Shared format helpers
# ---------------------------------------------------------------------------


def _write_output(text: str, path: str) -> None:
    if path == "-":
        print(text)
    else:
        Path(path).write_text(text)


def _format_report(report: BacktestReport, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(_report_to_dict(report), indent=2, default=str)
    return _report_to_csv(report)


def _report_to_dict(report: BacktestReport) -> dict:
    return {
        "strategy": report.strategy_name,
        "total_trades": report.total_trades,
        "winning_trades": report.winning_trades,
        "losing_trades": report.losing_trades,
        "win_rate": report.win_rate,
        "mean_return_pct": report.mean_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "profit_factor": report.profit_factor,
        "stop_wins": report.stop_wins,
        "stop_losses": report.stop_losses,
        "eod_wins": report.eod_wins,
        "eod_losses": report.eod_losses,
        "avg_hold_minutes": report.avg_hold_minutes,
        "avg_win_return_pct": report.avg_win_return_pct,
        "avg_loss_return_pct": report.avg_loss_return_pct,
        "max_consecutive_losses": report.max_consecutive_losses,
        "max_consecutive_wins": report.max_consecutive_wins,
        "profit_target_wins": report.profit_target_wins,
        "profit_target_losses": report.profit_target_losses,
        "expectancy_pct": report.expectancy_pct,
        "trades": [_trade_to_dict(t) for t in report.trades],
    }


def _trade_to_dict(t: ReplayTradeRecord) -> dict:
    return {
        "symbol": t.symbol,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat(),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "quantity": t.quantity,
        "exit_reason": t.exit_reason,
        "pnl": round(t.pnl, 4),
        "return_pct": round(t.return_pct, 6),
    }


def _report_to_csv(report: BacktestReport) -> str:
    buf = io.StringIO()
    buf.write(f"# strategy: {report.strategy_name}\n")
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "symbol", "entry_time", "exit_time", "entry_price",
            "exit_price", "quantity", "exit_reason", "pnl", "return_pct",
        ],
    )
    writer.writeheader()
    for t in report.trades:
        writer.writerow(_trade_to_dict(t))
    return buf.getvalue()


def _format_compare_json(reports: list[BacktestReport]) -> str:
    rows = [_compare_row(r) for r in reports]
    return json.dumps(rows, indent=2)


def _format_compare_csv(reports: list[BacktestReport]) -> str:
    fieldnames = [
        "strategy", "total_trades", "win_rate",
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio", "profit_factor",
        "stop_wins", "stop_losses", "eod_wins", "eod_losses",
        "profit_target_wins", "profit_target_losses",
        "avg_hold_minutes", "avg_win_return_pct", "avg_loss_return_pct",
        "expectancy_pct", "max_consecutive_losses", "max_consecutive_wins",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in reports:
        row = _compare_row(r)
        writer.writerow({k: ("" if row[k] is None else row[k]) for k in fieldnames})
    return buf.getvalue()


def _compare_row(report: BacktestReport) -> dict:
    return {
        "strategy": report.strategy_name,
        "total_trades": report.total_trades,
        "win_rate": report.win_rate,
        "mean_return_pct": report.mean_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "profit_factor": report.profit_factor,
        "stop_wins": report.stop_wins,
        "stop_losses": report.stop_losses,
        "eod_wins": report.eod_wins,
        "eod_losses": report.eod_losses,
        "avg_hold_minutes": report.avg_hold_minutes,
        "avg_win_return_pct": report.avg_win_return_pct,
        "avg_loss_return_pct": report.avg_loss_return_pct,
        "max_consecutive_losses": report.max_consecutive_losses,
        "max_consecutive_wins": report.max_consecutive_wins,
        "profit_target_wins": report.profit_target_wins,
        "profit_target_losses": report.profit_target_losses,
        "expectancy_pct": report.expectancy_pct,
    }


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

from alpaca_bot.config import Settings
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

    args = parser.parse_args(argv)

    if args.subcommand == "run":
        return _cmd_run(args)
    if args.subcommand == "compare":
        return _cmd_compare(args)
    if args.subcommand == "sweep":
        return _cmd_sweep(args)
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
        "mean_return_pct", "max_drawdown_pct", "sharpe_ratio",
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
    }

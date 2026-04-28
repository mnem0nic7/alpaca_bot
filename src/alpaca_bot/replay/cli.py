from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from alpaca_bot.config import Settings
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord
from alpaca_bot.replay.runner import ReplayRunner
from alpaca_bot.strategy import STRATEGY_REGISTRY


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-backtest")
    parser.add_argument("--scenario", required=True, metavar="FILE")
    parser.add_argument("--output", metavar="FILE", default="-", help="output file (default: stdout)")
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGY_REGISTRY),
        default=None,
        help="strategy to backtest (default: breakout)",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    strategy_name = args.strategy or "breakout"
    signal_evaluator = STRATEGY_REGISTRY[args.strategy] if args.strategy else None
    runner = ReplayRunner(settings, signal_evaluator=signal_evaluator, strategy_name=strategy_name)
    scenario = runner.load_scenario(args.scenario)
    result = runner.run(scenario)
    report: BacktestReport = result.backtest_report  # type: ignore[assignment]

    out_text = _format_report(report, args.format)
    if args.output == "-":
        print(out_text)
    else:
        Path(args.output).write_text(out_text)
    return 0


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
    import io
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

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from typing import Sequence

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.models import EQUITY_SESSION_STATE_STRATEGY_NAME
from alpaca_bot.storage.repositories import DailySessionStateStore, OrderStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alpaca-bot-session-eval",
        description="Evaluate a live trading session from Postgres data",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Session date (default: today)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode (default: paper)")
    parser.add_argument("--strategy-version", metavar="VERSION",
                        help="Strategy version (default: STRATEGY_VERSION env var)")
    parser.add_argument("--strategy", metavar="NAME", help="Filter to a single strategy name")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    eval_date: date = date.fromisoformat(args.date) if args.date else date.today()

    settings = Settings.from_env()
    strategy_version = args.strategy_version or settings.strategy_version
    trading_mode = TradingMode(args.mode)

    conn = connect_postgres(settings.database_url)
    try:
        order_store = OrderStore(conn)
        session_store = DailySessionStateStore(conn)

        state = session_store.load(
            session_date=eval_date,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
        )
        if state is None or state.equity_baseline is None:
            print(f"Warning: no equity baseline found for {eval_date}; using $100,000 as starting equity.")
            starting_equity = 100_000.0
        else:
            starting_equity = state.equity_baseline

        raw_trades = order_store.list_closed_trades(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            session_date=eval_date,
            strategy_name=args.strategy,
        )
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    if not raw_trades:
        strategy_label = f" (strategy={args.strategy})" if args.strategy else ""
        print(f"No closed trades for {eval_date}{strategy_label}.")
        return 0

    trade_records = [_row_to_trade_record(row) for row in raw_trades]
    report = report_from_records(
        trade_records,
        starting_equity=starting_equity,
        strategy_name=args.strategy or "all",
    )
    _print_session_report(report, eval_date=eval_date, trading_mode=args.mode,
                          strategy_version=strategy_version)
    return 0


def _row_to_trade_record(row: dict) -> ReplayTradeRecord:
    entry = row["entry_fill"]
    exit_ = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_ - entry) * qty
    return_pct = (exit_ - entry) / entry
    exit_reason = "stop" if row.get("intent_type") == "stop" else "eod"
    return ReplayTradeRecord(
        symbol=row["symbol"],
        entry_price=entry,
        exit_price=exit_,
        quantity=qty,
        entry_time=row["entry_time"],
        exit_time=row["exit_time"],
        exit_reason=exit_reason,
        pnl=pnl,
        return_pct=return_pct,
    )


def _print_session_report(
    report: BacktestReport,
    *,
    eval_date: date,
    trading_mode: str,
    strategy_version: str,
) -> None:
    header = f"Session Evaluation — {eval_date}  [{trading_mode} / {strategy_version}]"
    bar = "═" * len(header)
    print(f"\n{header}")
    print(bar)

    win_rate_str = f"{report.win_rate:.0%}" if report.win_rate is not None else "—"
    sharpe_str = f"{report.sharpe_ratio:.2f}" if report.sharpe_ratio is not None else "—"
    pf_str = f"{report.profit_factor:.2f}" if report.profit_factor is not None else "—"
    mean_str = (
        (f"+{report.mean_return_pct:.2%}" if report.mean_return_pct >= 0
         else f"{report.mean_return_pct:.2%}")
        if report.mean_return_pct is not None else "—"
    )
    dd_str = f"{report.max_drawdown_pct:.2%}" if report.max_drawdown_pct is not None else "—"
    hold_str = f"{report.avg_hold_minutes:.0f}min" if report.avg_hold_minutes is not None else "—"
    total_pnl = sum(t.pnl for t in report.trades)
    pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"

    print(f" Trades: {report.total_trades:3d}  Wins: {report.winning_trades:2d}  Losses: {report.losing_trades:2d}  Win rate: {win_rate_str}")
    print(f" P&L:    {pnl_str:>9s}  Sharpe: {sharpe_str:>5s}  Prof.fac: {pf_str:>5s}")
    print(f" Mean:   {mean_str:>9s}  Max DD: {dd_str:>5s}  Avg hold: {hold_str}")
    print(f" MaxCL:  {report.max_consecutive_losses:2d}        MaxCW: {report.max_consecutive_wins:2d}")

    print()
    print(" Exit breakdown:")
    print(f"   Stop wins: {report.stop_wins:3d}   Stop losses: {report.stop_losses:3d}")
    print(f"   EOD wins:  {report.eod_wins:3d}   EOD losses:  {report.eod_losses:3d}")

    if report.trades:
        print()
        print(f" {'Symbol':<8} {'Strategy':<12} {'Qty':>4}  {'Entry':>7}  {'Exit':>7}  {'P&L':>9}  {'Ret%':>7}  {'Hold':>5}  Exit")
        print(f" {'-'*8} {'-'*12} {'-'*4}  {'-'*7}  {'-'*7}  {'-'*9}  {'-'*7}  {'-'*5}  ----")
        for t in report.trades:
            hold_m = (t.exit_time - t.entry_time).total_seconds() / 60
            pnl_sign = "+" if t.pnl >= 0 else "-"
            pnl_t = f"{pnl_sign}${abs(t.pnl):.2f}"
            ret_sign = "+" if t.return_pct >= 0 else ""
            ret_t = f"{ret_sign}{t.return_pct:.2%}"
            print(f" {t.symbol:<8} {report.strategy_name:<12} {t.quantity:>4}  {t.entry_price:>7.2f}  {t.exit_price:>7.2f}  {pnl_t:>9}  {ret_t:>7}  {hold_m:>4.0f}m  {t.exit_reason}")
    print()

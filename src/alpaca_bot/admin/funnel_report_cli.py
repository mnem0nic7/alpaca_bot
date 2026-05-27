from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Sequence

from alpaca_bot.config import Settings
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import DecisionLogStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alpaca-bot-funnel-report",
        description="Per-strategy signal funnel from decision_log",
    )
    parser.add_argument("--days", type=int, default=7,
                        help="Number of trailing days to include (default: 7)")
    parser.add_argument("--start", metavar="YYYY-MM-DD",
                        help="Start date (overrides --days)")
    parser.add_argument("--end", metavar="YYYY-MM-DD",
                        help="End date (default: today; requires --start)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode filter (default: paper)")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    end_date = date.fromisoformat(args.end) if args.end else date.today()
    start_date = (
        date.fromisoformat(args.start) if args.start
        else end_date - timedelta(days=args.days - 1)
    )

    settings = Settings.from_env()
    conn = connect_postgres(settings.database_url)
    try:
        store = DecisionLogStore(conn)
        rows = store.funnel_by_strategy(
            start_date=start_date,
            end_date=end_date,
            trading_mode=args.mode,
            market_timezone=settings.market_timezone.key,
        )
    finally:
        close_fn = getattr(conn, "close", None)
        if callable(close_fn):
            close_fn()

    _print_table(rows, start_date, end_date)
    return 0


def _print_table(rows: list[dict], start_date: date, end_date: date) -> None:
    header = f" Signal Funnel  {start_date} → {end_date}"
    print()
    print(header)
    print(" " + "─" * (len(header) - 1))

    if not rows:
        print(" (no decision_log rows in period)")
        print()
        return

    col_w = 20
    num_w = 7
    print(
        f" {'Strategy':<{col_w}} "
        f"{'Eval':>{num_w}} "
        f"{'NotSkip':>{num_w}} "
        f"{'!PreFlt':>{num_w}} "
        f"{'Signal':>{num_w}} "
        f"{'!VWAP':>{num_w}} "
        f"{'Sized':>{num_w}} "
        f"{'Accept':>{num_w}}"
    )
    print(
        f" {'-'*col_w} "
        + (f"{'-'*num_w} " * 7).rstrip()
    )

    for row in rows:
        name = row["strategy_name"] or "(unknown)"
        print(
            f" {name:<{col_w}} "
            f"{row['evaluated']:>{num_w}} "
            f"{row['not_skipped']:>{num_w}} "
            f"{row['not_prefiltered']:>{num_w}} "
            f"{row['signal_fired']:>{num_w}} "
            f"{row['passed_entry_filter']:>{num_w}} "
            f"{row['sized']:>{num_w}} "
            f"{row['accepted']:>{num_w}}"
        )
    print()

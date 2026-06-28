from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage.db import connect_postgres
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    OptionOrderRepository,
    OrderStore,
)


@dataclass(frozen=True)
class EquityStrategyStats:
    strategy_name: str
    trades: int
    winning_trades: int
    total_pnl: float
    profit_factor: float | None
    expectancy_pct: float | None
    avg_hold_minutes: float | None


@dataclass(frozen=True)
class OptionUnderlyingStats:
    underlying: str
    strategy_name: str
    contracts: int
    premium_collected: float
    close_cost: float
    net_pnl: float
    retention_pct: float


def compute_equity_stats(records: list[dict]) -> list[EquityStrategyStats]:
    """Group equity trade records by strategy_name, compute per-strategy metrics."""
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_strategy[r["strategy_name"]].append(r)

    result = []
    for name, trades in sorted(by_strategy.items()):
        pnls = [t["pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else None

        win_returns = [
            (t["exit_price"] - t["entry_price"]) / t["entry_price"]
            for t in trades if t["pnl"] > 0
        ]
        loss_returns = [
            (t["exit_price"] - t["entry_price"]) / t["entry_price"]
            for t in trades if t["pnl"] <= 0
        ]
        if trades:
            win_rate = len(wins) / len(trades)
            avg_win = sum(win_returns) / len(win_returns) if win_returns else 0.0
            avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else 0.0
            expectancy_pct = win_rate * avg_win + (1 - win_rate) * avg_loss
        else:
            expectancy_pct = None

        holds = [t["hold_seconds"] for t in trades if t.get("hold_seconds") is not None]
        avg_hold_minutes = sum(holds) / len(holds) / 60 if holds else None

        result.append(EquityStrategyStats(
            strategy_name=name,
            trades=len(trades),
            winning_trades=len(wins),
            total_pnl=sum(pnls),
            profit_factor=profit_factor,
            expectancy_pct=expectancy_pct,
            avg_hold_minutes=avg_hold_minutes,
        ))
    return result


def compute_option_stats(records: list[dict]) -> list[OptionUnderlyingStats]:
    """Group option trade records by (underlying, strategy_name), compute premium retention."""
    key_map: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        key_map[(r["underlying"], r["strategy_name"])].append(r)

    result = []
    for (underlying, strategy), trades in sorted(key_map.items()):
        collected = sum(t["premium_collected"] for t in trades)
        cost = sum(t["close_cost"] for t in trades)
        net = sum(t["pnl"] for t in trades)
        retention = (net / collected * 100) if collected != 0 else 0.0
        result.append(OptionUnderlyingStats(
            underlying=underlying,
            strategy_name=strategy,
            contracts=sum(t["qty"] for t in trades),
            premium_collected=collected,
            close_cost=cost,
            net_pnl=net,
            retention_pct=retention,
        ))
    return result


def compute_daily_pnl(
    equity_records: list[dict],
    option_records: list[dict],
    market_timezone: str,
) -> dict[date, float]:
    """Sum net P&L (equity + option) by exit date in the given timezone."""
    tz = ZoneInfo(market_timezone)
    daily: dict[date, float] = defaultdict(float)
    for r in equity_records:
        d = r["exit_time"].astimezone(tz).date()
        daily[d] += r["pnl"]
    for r in option_records:
        d = r["closed_at"].astimezone(tz).date()
        daily[d] += r["pnl"]
    return dict(daily)


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    rng = hi - lo
    if rng == 0:
        return _SPARK_CHARS[3] * len(values)
    return "".join(_SPARK_CHARS[int((v - lo) / rng * 7)] for v in values)


def _fmt_pnl(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _render_header(
    since_date: date,
    until_date: date,
    trading_mode: str,
    strategy_version: str,
    equity_count: int,
    option_count: int,
    total_pnl: float,
) -> None:
    header = f"Strategy Report — {since_date} to {until_date}  [{trading_mode} / {strategy_version}]"
    print()
    print(header)
    print("═" * len(header))
    days = (until_date - since_date).days + 1
    pnl_str = _fmt_pnl(total_pnl)
    print(
        f" Period: {days} days   "
        f"Equity trades: {equity_count}   "
        f"Option contracts: {option_count}   "
        f"Total P&L: {pnl_str}"
    )


def _render_equity_table(stats: list[EquityStrategyStats]) -> None:
    print()
    print(" Equity Strategies")
    print(" " + "─" * 70)
    if not stats:
        print(" (no closed equity trades in period)")
        return
    print(f" {'Strategy':<20} {'Trades':>6}  {'Win%':>5}  {'P&L':>10}  {'PF':>5}  {'Expect%':>8}  {'AvgHold':>8}")
    print(f" {'-'*20} {'-'*6}  {'-'*5}  {'-'*10}  {'-'*5}  {'-'*8}  {'-'*8}")
    for s in stats:
        win_pct = f"{s.winning_trades / s.trades:.0%}" if s.trades else "—"
        pnl_str = _fmt_pnl(s.total_pnl)
        pf_str = f"{s.profit_factor:.2f}" if s.profit_factor is not None else "—"
        exp_str = (
            (f"+{s.expectancy_pct:.2%}" if s.expectancy_pct >= 0 else f"{s.expectancy_pct:.2%}")
            if s.expectancy_pct is not None else "—"
        )
        hold_str = f"{s.avg_hold_minutes:.0f}min" if s.avg_hold_minutes is not None else "—"
        print(f" {s.strategy_name:<20} {s.trades:>6}  {win_pct:>5}  {pnl_str:>10}  {pf_str:>5}  {exp_str:>8}  {hold_str:>8}")


def _render_option_table(stats: list[OptionUnderlyingStats]) -> None:
    print()
    print(" Option Premium")
    print(" " + "─" * 78)
    if not stats:
        print(" (no closed option positions in period)")
        return
    print(f" {'Underlying':<12} {'Strategy':<16} {'Cts':>4}  {'Collected':>10}  {'CloseCost':>10}  {'Net P&L':>10}  {'Retain%':>8}")
    print(f" {'-'*12} {'-'*16} {'-'*4}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*8}")
    for s in stats:
        ret_str = f"{s.retention_pct:+.0f}%"
        print(
            f" {s.underlying:<12} {s.strategy_name:<16} {s.contracts:>4}  "
            f"{_fmt_pnl(s.premium_collected):>10}  "
            f"${s.close_cost:>9,.2f}  "
            f"{_fmt_pnl(s.net_pnl):>10}  "
            f"{ret_str:>8}"
        )
    total_collected = sum(s.premium_collected for s in stats)
    total_cost = sum(s.close_cost for s in stats)
    total_net = sum(s.net_pnl for s in stats)
    total_ret = (total_net / total_collected * 100) if total_collected != 0 else 0.0
    print(f" {'TOTAL':<12} {'':<16} {sum(s.contracts for s in stats):>4}  "
          f"{_fmt_pnl(total_collected):>10}  "
          f"${total_cost:>9,.2f}  "
          f"{_fmt_pnl(total_net):>10}  "
          f"{total_ret:+.0f}%")


def _render_sparkline(daily_pnl: dict[date, float], since_date: date, until_date: date) -> None:
    print()
    print(" Daily P&L (last 14 trading days)")
    print(" " + "─" * 40)
    all_dates = sorted(daily_pnl.keys())
    last14 = all_dates[-14:] if len(all_dates) > 14 else all_dates
    if not last14:
        print(" (no data)")
        return
    values = [daily_pnl[d] for d in last14]
    spark = _sparkline(values)
    lo, hi = min(values), max(values)
    print(f" {spark}  [range: {_fmt_pnl(lo)} to {_fmt_pnl(hi)}]")
    if len(last14) >= 2:
        print(f" {last14[0].strftime('%m/%d'):<20} {last14[-1].strftime('%m/%d'):>20}")


def _render_operational_health(
    audit_store: AuditEventStore,
    since_dt: datetime,
    until_dt: datetime,
) -> None:
    print()
    print(" Operational Health (period)")
    print(" " + "─" * 40)

    cycles = audit_store.list_by_event_types(
        event_types=["supervisor_cycle"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    errors = audit_store.list_by_event_types(
        event_types=["supervisor_cycle_error", "strategy_cycle_error"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    dispatch_failures = audit_store.list_by_event_types(
        event_types=[
            "order_dispatch_failed",
            "order_dispatch_stop_price_rejected",
            "option_order_dispatch_failed",
        ],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    skipped_events = audit_store.list_by_event_types(
        event_types=["cycle_intent_skipped"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    options_skipped = sum(
        1 for e in skipped_events
        if e.payload.get("reason") == "options_market_closed"
    )
    stale_events = audit_store.list_by_event_types(
        event_types=["stale_positions_detected"],
        since=since_dt,
        until=until_dt,
        limit=10000,
    )
    stale_skipped = sum(
        1 for e in stale_events
        if e.payload.get("skipped_exit_option_count", 0) > 0
    )

    print(f" Total cycles:       {len(cycles):>6}")
    print(f" Cycle errors:       {len(errors):>6}")
    print(f" Dispatch failures:  {len(dispatch_failures):>6}")
    print(f" Skipped exits (OCC):{options_skipped:>6}     ← options-market-closed guard")
    print(f" Stale exits skipped:{stale_skipped:>6}     ← stale OCC positions")
    print()


def _export_csv(
    equity_records: list[dict],
    option_records: list[dict],
    daily_pnl: dict[date, float],
    csv_dir: str,
) -> None:
    os.makedirs(csv_dir, exist_ok=True)

    equity_path = os.path.join(csv_dir, "equity_trades.csv")
    equity_fields = ["symbol", "strategy_name", "qty", "entry_price", "exit_price",
                     "entry_time", "exit_time", "pnl", "hold_seconds"]
    with open(equity_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=equity_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(equity_records)
    print(f" Wrote {len(equity_records)} rows → {equity_path}")

    option_path = os.path.join(csv_dir, "option_trades.csv")
    option_fields = ["occ_symbol", "underlying", "strategy_name", "qty",
                     "premium_collected", "close_cost", "pnl", "opened_at", "closed_at"]
    with open(option_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=option_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(option_records)
    print(f" Wrote {len(option_records)} rows → {option_path}")

    daily_path = os.path.join(csv_dir, "daily_pnl.csv")
    with open(daily_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "net_pnl"])
        for d in sorted(daily_pnl.keys()):
            w.writerow([d.isoformat(), f"{daily_pnl[d]:.2f}"])
    print(f" Wrote {len(daily_pnl)} rows → {daily_path}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alpaca-bot-strategy-report",
        description="Multi-period strategy performance report from Postgres data",
    )
    parser.add_argument("--days", type=int, default=30, metavar="N",
                        help="Number of calendar days to look back (default: 30)")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Start date (overrides --days)")
    parser.add_argument("--until", metavar="YYYY-MM-DD",
                        help="End date inclusive (default: today ET)")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode (default: paper)")
    parser.add_argument("--strategy-version", metavar="VERSION",
                        help="Strategy version (default: STRATEGY_VERSION env var)")
    parser.add_argument("--csv-dir", metavar="PATH",
                        help="Export equity_trades.csv, option_trades.csv, daily_pnl.csv here")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    settings = Settings.from_env()
    tz = settings.market_timezone
    today_et = datetime.now(tz).date()
    until_date = date.fromisoformat(args.until) if args.until else today_et
    since_date = (
        date.fromisoformat(args.since)
        if args.since
        else until_date - timedelta(days=args.days - 1)
    )
    trading_mode = TradingMode(args.mode)
    strategy_version = args.strategy_version or settings.strategy_version
    market_timezone = settings.market_timezone.key

    since_dt = datetime.combine(since_date, time.min, tzinfo=tz).astimezone(timezone.utc)
    until_dt = datetime.combine(until_date + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)

    conn = connect_postgres(settings.database_url)
    try:
        order_store = OrderStore(conn)
        option_repo = OptionOrderRepository(conn)
        audit_store = AuditEventStore(conn)

        equity_records = order_store.list_closed_trade_records(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            since_date=since_date,
            until_date=until_date,
            market_timezone=market_timezone,
        )
        option_records = option_repo.list_closed_option_trade_records(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            since_date=since_date,
            until_date=until_date,
            market_timezone=market_timezone,
        )

        equity_stats = compute_equity_stats(equity_records)
        option_stats = compute_option_stats(option_records)
        daily_pnl = compute_daily_pnl(equity_records, option_records, market_timezone)

        total_pnl = (
            sum(r["pnl"] for r in equity_records)
            + sum(r["pnl"] for r in option_records)
        )
        total_option_contracts = sum(r["qty"] for r in option_records)

        _render_header(
            since_date=since_date,
            until_date=until_date,
            trading_mode=args.mode,
            strategy_version=strategy_version,
            equity_count=len(equity_records),
            option_count=total_option_contracts,
            total_pnl=total_pnl,
        )
        _render_equity_table(equity_stats)
        _render_option_table(option_stats)
        _render_sparkline(daily_pnl, since_date, until_date)
        _render_operational_health(audit_store, since_dt, until_dt)

        if args.csv_dir:
            _export_csv(equity_records, option_records, daily_pnl, args.csv_dir)

    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    return 0

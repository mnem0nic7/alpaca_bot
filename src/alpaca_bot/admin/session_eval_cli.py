from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator, Sequence
from zoneinfo import ZoneInfo

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.replay.report import BacktestReport, ReplayTradeRecord, report_from_records
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres, fetch_one
from alpaca_bot.storage.models import (
    EQUITY_SESSION_STATE_STRATEGY_NAME,
    AuditEvent,
    OrderRecord,
    PositionRecord,
)
from alpaca_bot.storage.repositories import (
    AuditEventStore,
    DailySessionStateStore,
    OrderStore,
    PositionStore,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="alpaca-bot-session-eval",
        description="Evaluate a live trading session from Postgres data",
    )
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Session date (default: today)")
    parser.add_argument(
        "--start-date",
        metavar="YYYY-MM-DD",
        help="First session date for a cumulative evaluation window.",
    )
    parser.add_argument(
        "--end-date",
        metavar="YYYY-MM-DD",
        help="Last session date for a cumulative evaluation window.",
    )
    parser.add_argument("--mode", default="paper", choices=["paper", "live"],
                        help="Trading mode (default: paper)")
    parser.add_argument("--strategy-version", metavar="VERSION",
                        help="Strategy version (default: STRATEGY_VERSION env var)")
    parser.add_argument("--strategy", metavar="NAME", help="Filter to a single strategy name")
    parser.add_argument(
        "--fail-below-pnl",
        type=float,
        metavar="DOLLARS",
        help="Exit non-zero when closed-trade P&L is below this threshold.",
    )
    parser.add_argument(
        "--min-trades-for-gate",
        type=int,
        default=1,
        metavar="N",
        help="Minimum closed trades required before --fail-below-pnl is enforced.",
    )
    parser.add_argument(
        "--require-min-trades",
        type=int,
        default=0,
        metavar="N",
        help="Exit non-zero unless at least N closed trades are present.",
    )
    parser.add_argument(
        "--fail-on-open-positions",
        action="store_true",
        help="Exit non-zero when positions remain open after the evaluated session.",
    )
    parser.add_argument(
        "--fail-on-diagnostics",
        action="store_true",
        help=(
            "Exit non-zero when operational diagnostics show missing cycles, "
            "blocked entries, runtime errors, stream issues, reconciliation issues, "
            "or missing decision activity."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    try:
        eval_start_date, eval_end_date = _resolve_eval_window(args)
    except ValueError as exc:
        parser.error(str(exc))
    eval_label = _date_label(eval_start_date, eval_end_date)

    settings = Settings.from_env()
    strategy_version = args.strategy_version or settings.strategy_version
    trading_mode = TradingMode(args.mode)
    market_timezone = settings.market_timezone.key

    conn = connect_postgres(settings.database_url)
    try:
        order_store = OrderStore(conn)
        session_store = DailySessionStateStore(conn)

        state = session_store.load(
            session_date=eval_start_date,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            strategy_name=EQUITY_SESSION_STATE_STRATEGY_NAME,
        )
        if state is None or state.equity_baseline is None:
            print(f"Warning: no equity baseline found for {eval_start_date}; using $100,000 as starting equity.")
            starting_equity = 100_000.0
        else:
            starting_equity = state.equity_baseline

        raw_trades = []
        for session_date in _date_range(eval_start_date, eval_end_date):
            raw_trades.extend(
                order_store.list_closed_trades(
                    trading_mode=trading_mode,
                    strategy_version=strategy_version,
                    session_date=session_date,
                    strategy_name=args.strategy,
                )
            )
        diagnostics = _build_session_diagnostics(
            conn,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            eval_start_date=eval_start_date,
            eval_end_date=eval_end_date,
            market_timezone=market_timezone,
            strategy_name=args.strategy,
        )
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()

    if not raw_trades:
        strategy_label = f" (strategy={args.strategy})" if args.strategy else ""
        print(f"No closed trades for {eval_label}{strategy_label}.")
        _print_session_diagnostics(diagnostics)
        if args.fail_on_open_positions and diagnostics.open_positions:
            _print_open_position_guard_failure(diagnostics)
            return 44
        if args.fail_on_diagnostics and diagnostics.has_guard_issues:
            _print_diagnostics_guard_failure(diagnostics)
            return 46
        if args.require_min_trades > 0:
            print(
                f"Proof incomplete: 0 closed trades below required "
                f"{args.require_min_trades}."
            )
            return 43
        return 0

    trade_records = [_row_to_trade_record(row) for row in raw_trades]
    report = report_from_records(
        trade_records,
        starting_equity=starting_equity,
        strategy_name=args.strategy or "all",
    )
    _print_session_report(report, eval_label=eval_label, trading_mode=args.mode,
                          strategy_version=strategy_version)
    _print_session_diagnostics(diagnostics)
    if args.fail_on_open_positions and diagnostics.open_positions:
        _print_open_position_guard_failure(diagnostics)
        return 44
    if report.total_trades < args.require_min_trades:
        print(
            f"Proof incomplete: {report.total_trades} closed trades below "
            f"required {args.require_min_trades}."
        )
        return 43
    if args.fail_below_pnl is not None:
        total_pnl = sum(t.pnl for t in report.trades)
        if report.total_trades >= args.min_trades_for_gate and total_pnl < args.fail_below_pnl:
            print(
                f"Guard failed: pnl=${total_pnl:.2f} below "
                f"${args.fail_below_pnl:.2f} after {report.total_trades} trades."
            )
            return 42
    if args.fail_on_diagnostics and diagnostics.has_guard_issues:
        _print_diagnostics_guard_failure(diagnostics)
        return 46
    return 0


def _resolve_eval_window(args: argparse.Namespace) -> tuple[date, date]:
    if args.end_date and not args.start_date:
        raise ValueError("--end-date requires --start-date")
    if not args.start_date:
        eval_date = _parse_iso_date(args.date, "--date") if args.date else date.today()
        return eval_date, eval_date

    start_date = _parse_iso_date(args.start_date, "--start-date")
    if args.end_date:
        end_date = _parse_iso_date(args.end_date, "--end-date")
    elif args.date:
        end_date = _parse_iso_date(args.date, "--date")
    else:
        end_date = date.today()
    if end_date < start_date:
        raise ValueError("--end-date must be on or after --start-date")
    return start_date, end_date


def _parse_iso_date(value: str, option_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{option_name} must use YYYY-MM-DD") from exc


def _date_range(start_date: date, end_date: date) -> Iterator[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _date_label(start_date: date, end_date: date) -> str:
    if start_date == end_date:
        return start_date.isoformat()
    return f"{start_date.isoformat()}..{end_date.isoformat()}"


def _print_open_position_guard_failure(diagnostics: SessionDiagnostics) -> None:
    symbols = ", ".join(position.symbol for position in diagnostics.open_positions)
    print(
        f"Guard failed: {len(diagnostics.open_positions)} open "
        f"position(s) remain after session: {symbols}."
    )


def _print_diagnostics_guard_failure(diagnostics: SessionDiagnostics) -> None:
    print("Guard failed: operational diagnostics contain proof-blocking issues.")


def _row_to_trade_record(row: dict) -> ReplayTradeRecord:
    entry = row["entry_fill"]
    exit_ = row["exit_fill"]
    qty = row["qty"]
    pnl = (exit_ - entry) * qty
    return_pct = (exit_ - entry) / entry
    # The orders table stores intent_type="exit" for both EOD and profit-target exits.
    # Distinguishing them requires a schema change (adding a reason column to orders).
    # Until then, profit_target_wins/losses will always be 0 in live session reports.
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


@dataclass
class DecisionActivityStats:
    cycles: int = 0
    records: int = 0
    accepted: int = 0
    latest_cycle_at: datetime | None = None


@dataclass
class SessionDiagnostics:
    cycle_errors: list[AuditEvent] = field(default_factory=list)
    dispatch_failures: list[AuditEvent] = field(default_factory=list)
    failed_entries: list[OrderRecord] = field(default_factory=list)
    stream_issues: list[AuditEvent] = field(default_factory=list)
    open_positions: list[PositionRecord] = field(default_factory=list)
    reconciliation_issues: list[AuditEvent] = field(default_factory=list)
    total_supervisor_cycles: int = 0
    entries_disabled_cycles: int = 0
    entries_disabled_reasons: dict[str, int] = field(default_factory=dict)
    strategy_name: str | None = None
    strategy_disabled_cycles: int = 0
    strategy_disabled_reasons: dict[str, int] = field(default_factory=dict)
    decision_activity: DecisionActivityStats = field(default_factory=DecisionActivityStats)

    @property
    def has_issues(self) -> bool:
        return any([
            self.cycle_errors,
            self.dispatch_failures,
            self.failed_entries,
            self.stream_issues,
            self.open_positions,
            self.reconciliation_issues,
            self.entries_disabled_cycles,
            self.strategy_disabled_cycles,
            self.total_supervisor_cycles == 0,
            self.total_supervisor_cycles > 0 and self.decision_activity.records == 0,
        ])

    @property
    def has_guard_issues(self) -> bool:
        """Return True for diagnostics that should fail an EOD proof guard.

        Unfilled entry orders remain diagnostic output only. A stop-limit entry
        can legitimately cancel without fill; missing cycles, disabled entries,
        runtime errors, stream/reconciliation issues, open exposure, and missing
        decision activity are proof-blocking.
        """
        return any([
            self.cycle_errors,
            self.dispatch_failures,
            self.stream_issues,
            self.open_positions,
            self.reconciliation_issues,
            self.entries_disabled_cycles,
            self.strategy_disabled_cycles,
            self.total_supervisor_cycles == 0,
            self.total_supervisor_cycles > 0 and self.decision_activity.records == 0,
        ])


def _build_session_diagnostics(
    conn: ConnectionProtocol,
    *,
    trading_mode: TradingMode,
    strategy_version: str,
    eval_start_date: date,
    eval_end_date: date,
    market_timezone: str,
    strategy_name: str | None = None,
) -> SessionDiagnostics:
    tz = ZoneInfo(market_timezone)
    session_start = datetime.combine(eval_start_date, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    session_end = datetime.combine(eval_end_date + timedelta(days=1), time(0, 0), tzinfo=tz).astimezone(timezone.utc)

    audit_store = AuditEventStore(conn)
    order_store = OrderStore(conn)
    position_store = PositionStore(conn)
    failed_entries: list[OrderRecord] = []
    for session_date in _date_range(eval_start_date, eval_end_date):
        failed_entries.extend(
            order_store.list_failed_entries(
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                session_date=session_date,
                market_timezone=market_timezone,
            )
        )
    total_cycles, disabled_cycles, disabled_reasons = _load_entries_disabled_cycle_stats(
        conn,
        session_start=session_start,
        session_end=session_end,
        trading_mode=trading_mode,
        strategy_version=strategy_version,
    )
    strategy_disabled_cycles, strategy_disabled_reasons = _load_strategy_disabled_cycle_stats(
        conn,
        session_start=session_start,
        session_end=session_end,
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
    )
    decision_activity = _load_decision_activity_stats(
        conn,
        session_start=session_start,
        session_end=session_end,
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
    )

    return SessionDiagnostics(
        cycle_errors=audit_store.list_by_event_types(
            event_types=["supervisor_cycle_error", "strategy_cycle_error"],
            since=session_start,
            until=session_end,
            limit=100,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        ),
        dispatch_failures=audit_store.list_by_event_types(
            event_types=["order_dispatch_failed"],
            since=session_start,
            until=session_end,
            limit=100,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        ),
        failed_entries=failed_entries,
        stream_issues=audit_store.list_by_event_types(
            event_types=["stream_heartbeat_stale", "stream_restart_failed", "trade_update_stream_failed"],
            since=session_start,
            until=session_end,
            limit=100,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        ),
        open_positions=position_store.list_all(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        ),
        reconciliation_issues=audit_store.list_by_event_types(
            event_types=["reconciliation_miss_count_incremented", "runtime_reconciliation_detected"],
            since=session_start,
            until=session_end,
            limit=100,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        ),
        total_supervisor_cycles=total_cycles,
        entries_disabled_cycles=disabled_cycles,
        entries_disabled_reasons=disabled_reasons,
        strategy_name=strategy_name,
        strategy_disabled_cycles=strategy_disabled_cycles,
        strategy_disabled_reasons=strategy_disabled_reasons,
        decision_activity=decision_activity,
    )


def _load_entries_disabled_cycle_stats(
    conn: ConnectionProtocol,
    *,
    session_start: datetime,
    session_end: datetime,
    trading_mode: TradingMode | str,
    strategy_version: str,
) -> tuple[int, int, dict[str, int]]:
    trading_mode_value = trading_mode.value if isinstance(trading_mode, TradingMode) else str(trading_mode)
    try:
        row = fetch_one(
            conn,
            """
            SELECT
                COUNT(*)::int,
                COUNT(*) FILTER (
                    WHERE (payload->>'entries_disabled')::boolean IS TRUE
                )::int,
                COALESCE((
                    SELECT string_agg(reason || ':' || reason_count::text, ',' ORDER BY reason)
                    FROM (
                        SELECT reason, COUNT(*)::int AS reason_count
                        FROM audit_events e
                        CROSS JOIN LATERAL jsonb_array_elements_text(
                            COALESCE(e.payload->'entries_disabled_reasons', '[]'::jsonb)
                        ) AS reason
                        WHERE e.event_type = 'supervisor_cycle'
                          AND e.created_at >= %s
                          AND e.created_at < %s
                          AND (NOT (e.payload ? 'trading_mode') OR e.payload->>'trading_mode' = %s)
                          AND (NOT (e.payload ? 'strategy_version') OR e.payload->>'strategy_version' = %s)
                          AND (e.payload->>'entries_disabled')::boolean IS TRUE
                        GROUP BY reason
                    ) reason_counts
                ), '') AS reason_summary
            FROM audit_events
            WHERE event_type = 'supervisor_cycle'
              AND created_at >= %s
              AND created_at < %s
              AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
              AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            """,
            (
                session_start,
                session_end,
                trading_mode_value,
                strategy_version,
                session_start,
                session_end,
                trading_mode_value,
                strategy_version,
            ),
        )
    except Exception:
        return (0, 0, {})
    if row is None:
        return (0, 0, {})
    reason_summary = str(row[2] or "")
    reasons: dict[str, int] = {}
    for part in (p for p in reason_summary.split(",") if p):
        reason, _, raw_count = part.rpartition(":")
        try:
            reasons[reason] = int(raw_count)
        except ValueError:
            continue
    return (int(row[0] or 0), int(row[1] or 0), reasons)


def _load_strategy_disabled_cycle_stats(
    conn: ConnectionProtocol,
    *,
    session_start: datetime,
    session_end: datetime,
    trading_mode: TradingMode | str,
    strategy_version: str,
    strategy_name: str | None,
) -> tuple[int, dict[str, int]]:
    if not strategy_name:
        return (0, {})
    trading_mode_value = trading_mode.value if isinstance(trading_mode, TradingMode) else str(trading_mode)
    try:
        row = fetch_one(
            conn,
            """
            WITH recent AS (
              SELECT event_type, payload
              FROM audit_events
              WHERE event_type = 'supervisor_cycle'
                AND created_at >= %s
                AND created_at < %s
                AND (NOT (payload ? 'trading_mode') OR payload->>'trading_mode' = %s)
                AND (NOT (payload ? 'strategy_version') OR payload->>'strategy_version' = %s)
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE COALESCE(payload->'blocked_strategy_names', '[]'::jsonb) ? %s
                )::int,
                COALESCE((
                    SELECT string_agg(reason || ':' || reason_count::text, ',' ORDER BY reason)
                    FROM (
                        SELECT reason, COUNT(*)::int AS reason_count
                        FROM recent r
                        CROSS JOIN LATERAL jsonb_array_elements_text(
                            COALESCE(
                                r.payload->'strategy_entries_disabled_reasons'->%s,
                                '[]'::jsonb
                            )
                        ) AS reason
                        WHERE COALESCE(r.payload->'blocked_strategy_names', '[]'::jsonb) ? %s
                        GROUP BY reason
                    ) reason_counts
                ), '') AS reason_summary
            FROM recent
            """,
            (
                session_start,
                session_end,
                trading_mode_value,
                strategy_version,
                strategy_name,
                strategy_name,
                strategy_name,
            ),
        )
    except Exception:
        return (0, {})
    if row is None:
        return (0, {})
    reason_summary = str(row[1] or "")
    reasons: dict[str, int] = {}
    for part in (p for p in reason_summary.split(",") if p):
        reason, _, raw_count = part.rpartition(":")
        try:
            reasons[reason] = int(raw_count)
        except ValueError:
            continue
    return (int(row[0] or 0), reasons)


def _load_decision_activity_stats(
    conn: ConnectionProtocol,
    *,
    session_start: datetime,
    session_end: datetime,
    trading_mode: TradingMode | str,
    strategy_version: str,
    strategy_name: str | None,
) -> DecisionActivityStats:
    trading_mode_value = trading_mode.value if isinstance(trading_mode, TradingMode) else str(trading_mode)
    try:
        row = fetch_one(
            conn,
            """
            SELECT
                COUNT(DISTINCT cycle_at)::int,
                COUNT(*)::int,
                COUNT(*) FILTER (WHERE decision = 'accepted')::int,
                MAX(cycle_at)
            FROM decision_log
            WHERE cycle_at >= %s
              AND cycle_at < %s
              AND trading_mode = %s
              AND strategy_version = %s
              AND (%s::text IS NULL OR strategy_name = %s)
            """,
            (
                session_start,
                session_end,
                trading_mode_value,
                strategy_version,
                strategy_name,
                strategy_name,
            ),
        )
    except Exception:
        return DecisionActivityStats()
    if row is None:
        return DecisionActivityStats()
    return DecisionActivityStats(
        cycles=int(row[0] or 0),
        records=int(row[1] or 0),
        accepted=int(row[2] or 0),
        latest_cycle_at=row[3],
    )


def _print_session_diagnostics(diagnostics: SessionDiagnostics) -> None:
    print()
    print(" Diagnostics")
    print(" " + "─" * 60)

    if not diagnostics.has_issues:
        print(" ✓ No operational issues found")
        _print_decision_activity(diagnostics)
        print()
        return

    if diagnostics.total_supervisor_cycles == 0:
        print(" ⚠ No supervisor cycles recorded for evaluated window")

    if diagnostics.cycle_errors:
        print(f" ⚠ Cycle errors: {len(diagnostics.cycle_errors)}")
        for e in diagnostics.cycle_errors[:3]:
            ts = e.created_at.strftime("%H:%M:%SZ")
            msg = str(e.payload.get("error", ""))[:60]
            print(f"     {ts} — {msg}")

    if diagnostics.dispatch_failures:
        print(f" ⚠ Dispatch failures: {len(diagnostics.dispatch_failures)}")
        for e in diagnostics.dispatch_failures[:3]:
            sym = e.symbol or str(e.payload.get("symbol", "?"))
            msg = str(e.payload.get("error", ""))[:40]
            print(f"     {sym}: {msg}")

    if diagnostics.failed_entries:
        parts = []
        for o in diagnostics.failed_entries:
            if o.filled_quantity is not None and o.filled_quantity > 0:
                parts.append(f"{o.symbol} (partial {o.status})")
            else:
                parts.append(f"{o.symbol} ({o.status})")
        print(f" ⚠ Unfilled entries: {', '.join(parts)}")

    if diagnostics.stream_issues:
        print(f" ⚠ Stream interruptions: {len(diagnostics.stream_issues)}")

    if diagnostics.open_positions:
        syms = [p.symbol for p in diagnostics.open_positions]
        print(f" ⚠ Open positions at EOD: {', '.join(syms)}")

    if diagnostics.reconciliation_issues:
        print(f" ⚠ Reconciliation issues: {len(diagnostics.reconciliation_issues)}")

    if diagnostics.entries_disabled_cycles:
        print(
            " ⚠ Entries disabled cycles: "
            f"{diagnostics.entries_disabled_cycles}/{diagnostics.total_supervisor_cycles}"
        )
        if diagnostics.entries_disabled_reasons:
            rendered = ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(diagnostics.entries_disabled_reasons.items())
            )
            print(f"     Reasons: {rendered}")

    if diagnostics.strategy_disabled_cycles:
        label = diagnostics.strategy_name or "strategy"
        print(
            f" ⚠ {label} entries blocked cycles: "
            f"{diagnostics.strategy_disabled_cycles}/{diagnostics.total_supervisor_cycles}"
        )
        if diagnostics.strategy_disabled_reasons:
            rendered = ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(diagnostics.strategy_disabled_reasons.items())
            )
            print(f"     Reasons: {rendered}")

    _print_decision_activity(diagnostics)

    print()


def _print_decision_activity(diagnostics: SessionDiagnostics) -> None:
    activity = diagnostics.decision_activity
    strategy_label = (
        f"{diagnostics.strategy_name} decision activity"
        if diagnostics.strategy_name
        else "Decision activity"
    )
    if activity.records > 0:
        latest = activity.latest_cycle_at.isoformat() if activity.latest_cycle_at else "unknown"
        print(
            f" - {strategy_label}: cycles={activity.cycles} "
            f"records={activity.records} accepted={activity.accepted} latest={latest}"
        )
    elif diagnostics.total_supervisor_cycles > 0:
        print(f" ⚠ {strategy_label}: no decision_log rows")


def _print_session_report(
    report: BacktestReport,
    *,
    eval_label: str,
    trading_mode: str,
    strategy_version: str,
) -> None:
    header = f"Session Evaluation — {eval_label}  [{trading_mode} / {strategy_version}]"
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

    expectancy_str = (
        (f"+{report.expectancy_pct:.2%}" if report.expectancy_pct >= 0 else f"{report.expectancy_pct:.2%}")
        if report.expectancy_pct is not None else "—"
    )
    print(f" Expectancy: {expectancy_str}  (positive = edge exists)")

    print()
    print(" Exit breakdown:")
    print(f"   Stop wins:   {report.stop_wins:3d}   Stop losses:   {report.stop_losses:3d}")
    print(f"   EOD wins:    {report.eod_wins:3d}   EOD losses:    {report.eod_losses:3d}")
    print(f"   Target wins: {report.profit_target_wins:3d}   Target losses: {report.profit_target_losses:3d}")

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

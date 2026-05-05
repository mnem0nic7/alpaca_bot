from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timezone
import sys
from typing import Callable, Sequence, TextIO

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.notifications import Notifier
from alpaca_bot.notifications.factory import build_notifier
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    OrderRecord,
    OrderStore,
    PositionRecord,
    PositionStore,
    StrategyFlag,
    StrategyFlagStore,
    TradingStatus,
    TradingStatusStore,
    TradingStatusValue,
)
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres
from alpaca_bot.strategy import STRATEGY_REGISTRY


def build_parser(settings: Settings | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alpaca-bot-admin")
    defaults = settings or _fallback_settings()
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("status", "halt", "close-only", "resume"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument(
            "--mode",
            choices=[mode.value for mode in TradingMode],
            default=defaults.trading_mode.value,
        )
        subparser.add_argument(
            "--strategy-version",
            default=defaults.strategy_version,
        )
        if name == "halt":
            subparser.add_argument("--reason", required=True)
        elif name in {"close-only", "resume"}:
            subparser.add_argument("--reason")

    for name in ("enable-strategy", "disable-strategy"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument(
            "strategy_name",
            choices=list(STRATEGY_REGISTRY),
        )
        subparser.add_argument(
            "--mode",
            choices=[mode.value for mode in TradingMode],
            default=defaults.trading_mode.value,
        )
        subparser.add_argument(
            "--strategy-version",
            default=defaults.strategy_version,
        )

    ce_parser = subparsers.add_parser("close-excess")
    ce_parser.add_argument(
        "--mode",
        choices=[mode.value for mode in TradingMode],
        default=defaults.trading_mode.value,
    )
    ce_parser.add_argument("--strategy-version", default=defaults.strategy_version)
    ce_parser.add_argument("--keep", type=int, default=20)
    ce_parser.add_argument("--dry-run", action="store_true")

    return parser


def run_admin_command(
    argv: Sequence[str],
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    now: datetime | None = None,
    notifier: Notifier | None = None,
) -> str:
    args = build_parser(settings).parse_args(list(argv))
    timestamp = now or datetime.now(timezone.utc)
    trading_mode = TradingMode(args.mode)
    strategy_version = args.strategy_version
    status_store = TradingStatusStore(connection)
    event_store = AuditEventStore(connection)

    if args.command == "status":
        current = status_store.load(
            trading_mode=trading_mode,
            strategy_version=strategy_version,
        )
        if current is None:
            return (
                f"mode={trading_mode.value} "
                f"strategy={strategy_version} status=unknown"
            )
        return (
            f"mode={current.trading_mode.value} "
            f"strategy={current.strategy_version} "
            f"status={current.status.value} "
            f"kill_switch={str(current.kill_switch_enabled).lower()} "
            f"reason={current.status_reason or '-'} "
            f"updated_at={current.updated_at.isoformat()}"
        )

    if args.command == "halt":
        result = _write_status_change(
            connection=connection,
            settings=settings,
            status_store=status_store,
            event_store=event_store,
            new_status=TradingStatusValue.HALTED,
            command_name="halt",
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            reason=args.reason,
            now=timestamp,
            kill_switch_enabled=True,
        )
        if notifier is not None:
            notifier.send(
                subject="Trading halted",
                body=f"mode={trading_mode.value} strategy={strategy_version} reason={args.reason}",
            )
        return result

    if args.command == "close-only":
        result = _write_status_change(
            connection=connection,
            settings=settings,
            status_store=status_store,
            event_store=event_store,
            new_status=TradingStatusValue.CLOSE_ONLY,
            command_name="close-only",
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            reason=args.reason,
            now=timestamp,
            kill_switch_enabled=False,
        )
        if notifier is not None:
            notifier.send(
                subject="Trading set to close-only",
                body=f"mode={trading_mode.value} strategy={strategy_version} reason={args.reason or '-'}",
            )
        return result

    if args.command == "resume":
        result = _write_status_change(
            connection=connection,
            settings=settings,
            status_store=status_store,
            event_store=event_store,
            new_status=TradingStatusValue.ENABLED,
            command_name="resume",
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            reason=args.reason,
            now=timestamp,
            kill_switch_enabled=False,
        )
        if notifier is not None:
            notifier.send(
                subject="Trading resumed",
                body=f"mode={trading_mode.value} strategy={strategy_version} reason={args.reason or '-'}",
            )
        return result

    if args.command in ("enable-strategy", "disable-strategy"):
        enabled = args.command == "enable-strategy"
        return _write_strategy_flag(
            connection=connection,
            event_store=event_store,
            strategy_name=args.strategy_name,
            trading_mode=trading_mode,
            strategy_version=strategy_version,
            enabled=enabled,
            now=timestamp,
        )

    raise ValueError(f"Unsupported command: {args.command}")


def _write_strategy_flag(
    *,
    connection: ConnectionProtocol,
    event_store: AuditEventStore,
    strategy_name: str,
    trading_mode: TradingMode,
    strategy_version: str,
    enabled: bool,
    now: datetime,
) -> str:
    flag = StrategyFlag(
        strategy_name=strategy_name,
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        enabled=enabled,
        updated_at=now,
    )
    flag_store = StrategyFlagStore(connection)
    flag_store.save(flag, commit=False)
    event_store.append(
        AuditEvent(
            event_type="strategy_flag_changed",
            payload={
                "strategy_name": strategy_name,
                "trading_mode": trading_mode.value,
                "strategy_version": strategy_version,
                "enabled": str(enabled).lower(),
            },
            created_at=now,
        ),
        commit=False,
    )
    connection.commit()
    action = "enabled" if enabled else "disabled"
    return (
        f"strategy={strategy_name} "
        f"mode={trading_mode.value} "
        f"version={strategy_version} "
        f"{action}"
    )


def _write_status_change(
    *,
    connection: ConnectionProtocol,
    settings: Settings,
    status_store: TradingStatusStore,
    event_store: AuditEventStore,
    new_status: TradingStatusValue,
    command_name: str,
    trading_mode: TradingMode,
    strategy_version: str,
    reason: str | None,
    now: datetime,
    kill_switch_enabled: bool,
) -> str:
    del settings
    status = TradingStatus(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        status=new_status,
        kill_switch_enabled=kill_switch_enabled,
        status_reason=reason or None,
        updated_at=now,
    )
    # Write status and audit event atomically so a crash between the two cannot
    # leave trading_status changed with no corresponding audit trail.
    status_store.save(status, commit=False)
    event_store.append(
        AuditEvent(
            event_type="trading_status_changed",
            payload=_status_change_payload(
                command_name=command_name,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                new_status=new_status,
                reason=reason,
            ),
            created_at=now,
        ),
        commit=False,
    )
    connection.commit()
    return (
        f"mode={trading_mode.value} "
        f"strategy={strategy_version} "
        f"status={new_status.value.upper()} "
        f"reason={reason or '-'}"
    )


def _status_change_payload(
    *,
    command_name: str,
    trading_mode: TradingMode,
    strategy_version: str,
    new_status: TradingStatusValue,
    reason: str | None,
) -> dict[str, str]:
    payload = {
        "command": command_name,
        "trading_mode": trading_mode.value,
        "strategy_version": strategy_version,
        "status": new_status.value,
    }
    if reason is not None:
        payload["reason"] = reason
    return payload


def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable[[], ConnectionProtocol] | None = None,
    trading_status_store_factory: Callable[[ConnectionProtocol], TradingStatusStore] = TradingStatusStore,
    audit_event_store_factory: Callable[[ConnectionProtocol], AuditEventStore] = AuditEventStore,
    now: Callable[[], datetime] | None = None,
    stdout: TextIO | None = None,
    settings: Settings | None = None,
    notifier: Notifier | None = None,
    broker_factory: Callable[["Settings"], object] | None = None,
    position_store_factory: Callable[[ConnectionProtocol], PositionStore] = PositionStore,
    order_store_factory: Callable[[ConnectionProtocol], OrderStore] = OrderStore,
) -> int:
    parsed_argv = list(sys.argv[1:] if argv is None else argv)
    if settings is not None:
        resolved_settings = settings
    elif connect is not None:
        resolved_settings = _fallback_settings()
    else:
        resolved_settings = Settings.from_env()
    _notifier = notifier if notifier is not None else build_notifier(resolved_settings)
    output: str | None = None
    connection = connect() if connect is not None else connect_postgres(resolved_settings.database_url)
    try:
        args = build_parser(resolved_settings).parse_args(parsed_argv)
        timestamp = now() if now is not None else datetime.now(timezone.utc)
        trading_mode = TradingMode(args.mode)
        strategy_version = args.strategy_version
        status_store = trading_status_store_factory(connection)
        audit_store = audit_event_store_factory(connection)
        if args.command == "status":
            current = status_store.load(
                trading_mode=trading_mode,
                strategy_version=strategy_version,
            )
            if current is None:
                output = (
                    f"mode={trading_mode.value} "
                    f"strategy={strategy_version} status=unknown"
                )
            else:
                output = (
                    f"mode={current.trading_mode.value} "
                    f"strategy={current.strategy_version} "
                    f"status={current.status.value} "
                    f"kill_switch={str(current.kill_switch_enabled).lower()} "
                    f"reason={current.status_reason or '-'} "
                    f"updated_at={current.updated_at.isoformat()}"
                )
        elif args.command in ("enable-strategy", "disable-strategy"):
            output = _write_strategy_flag(
                connection=connection,
                event_store=audit_store,
                strategy_name=args.strategy_name,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                enabled=args.command == "enable-strategy",
                now=timestamp,
            )
        elif args.command == "close-excess":
            _broker = (
                broker_factory(resolved_settings)
                if broker_factory is not None
                else _make_default_broker(resolved_settings)
            )
            _run_close_excess(
                position_store=position_store_factory(connection),
                order_store=order_store_factory(connection),
                audit_store=audit_store,
                broker=_broker,
                keep=args.keep,
                dry_run=args.dry_run,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                now=timestamp,
                stdout=stdout or sys.stdout,
            )
        else:
            command_reason = getattr(args, "reason", None)
            if args.command == "halt":
                status_value = TradingStatusValue.HALTED
                kill_switch_enabled = True
            elif args.command == "close-only":
                status_value = TradingStatusValue.CLOSE_ONLY
                kill_switch_enabled = False
            elif args.command == "resume":
                status_value = TradingStatusValue.ENABLED
                kill_switch_enabled = False
            else:
                raise ValueError(f"Unsupported command: {args.command}")

            output = _write_status_change(
                connection=connection,
                settings=resolved_settings,
                status_store=status_store,
                event_store=audit_store,
                new_status=status_value,
                command_name=args.command,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                reason=command_reason or None,
                now=timestamp,
                kill_switch_enabled=kill_switch_enabled,
            )
            if _notifier is not None:
                _subjects = {
                    "halt": "Trading halted",
                    "close-only": "Trading set to close-only",
                    "resume": "Trading resumed",
                }
                try:
                    _notifier.send(
                        subject=_subjects.get(args.command, f"Trading status changed: {args.command}"),
                        body=f"mode={trading_mode.value} strategy={strategy_version} reason={command_reason or '-'}",
                    )
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Notifier send failed in admin CLI")
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    if output is not None:
        print(output, file=stdout or sys.stdout)
    return 0


def _run_close_excess(
    *,
    position_store: PositionStore,
    order_store: OrderStore,
    audit_store: AuditEventStore,
    broker: object,
    keep: int,
    dry_run: bool,
    trading_mode: TradingMode,
    strategy_version: str,
    now: datetime,
    stdout: TextIO,
) -> None:
    positions = position_store.list_all(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
    )

    def _stop_pct(p: PositionRecord) -> float:
        return (p.entry_price - p.stop_price) / p.entry_price

    ranked = sorted(positions, key=_stop_pct)
    keep_symbols = {p.symbol for p in ranked[:keep]}
    to_close = ranked[keep:]

    for position in ranked:
        label = "KEEP" if position.symbol in keep_symbols else "CLOSE"
        print(
            f"{label}  {position.symbol}  stop_pct={round(_stop_pct(position) * 100, 2):.2f}%",
            file=stdout,
        )

    if dry_run or not to_close:
        return

    entry_orders = order_store.list_by_status(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        statuses=["new", "pending_submit", "partially_filled"],
    )
    stop_orders = order_store.list_by_status(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        statuses=["new", "pending_submit"],
    )

    for position in to_close:
        pct = _stop_pct(position)

        for order in entry_orders:
            if (
                order.intent_type == "entry"
                and order.symbol == position.symbol
                and order.broker_order_id
            ):
                broker.cancel_order(order.broker_order_id)  # type: ignore[union-attr]

        for order in stop_orders:
            if order.intent_type == "stop" and order.symbol == position.symbol:
                if order.broker_order_id:
                    broker.cancel_order(order.broker_order_id)  # type: ignore[union-attr]
                order_store.save(
                    dataclasses.replace(order, status="canceled", updated_at=now),
                    commit=False,
                )

        client_order_id = (
            f"{strategy_version}:{position.symbol}:force_exit:{now.isoformat()}"
        )
        broker_order = broker.submit_market_exit(  # type: ignore[union-attr]
            symbol=position.symbol,
            quantity=position.quantity,
            client_order_id=client_order_id,
        )
        order_store.save(
            OrderRecord(
                client_order_id=client_order_id,
                symbol=position.symbol,
                side="sell",
                intent_type="exit",
                status=broker_order.status,
                quantity=position.quantity,
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                broker_order_id=broker_order.broker_order_id,
                created_at=now,
                updated_at=now,
            ),
            commit=False,
        )
        audit_store.append(
            AuditEvent(
                event_type="position_force_closed",
                symbol=position.symbol,
                payload={
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "entry_price": str(position.entry_price),
                    "stop_pct": str(round(pct * 100, 2)),
                },
                created_at=now,
            ),
            commit=True,
        )


def _make_default_broker(settings: Settings) -> object:
    from alpaca_bot.execution.alpaca import AlpacaBroker  # noqa: PLC0415

    return AlpacaBroker.from_settings(settings)


def _fallback_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "iex",
            "SYMBOLS": "AAPL,MSFT,SPY",
            "DAILY_SMA_PERIOD": "20",
            "BREAKOUT_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_LOOKBACK_BARS": "20",
            "RELATIVE_VOLUME_THRESHOLD": "1.5",
            "ENTRY_TIMEFRAME_MINUTES": "15",
            "RISK_PER_TRADE_PCT": "0.0025",
            "MAX_POSITION_PCT": "0.05",
            "MAX_OPEN_POSITIONS": "3",
            "DAILY_LOSS_LIMIT_PCT": "0.01",
            "STOP_LIMIT_BUFFER_PCT": "0.001",
            "BREAKOUT_STOP_BUFFER_PCT": "0.001",
            "ENTRY_STOP_PRICE_BUFFER": "0.01",
            "ENTRY_WINDOW_START": "10:00",
            "ENTRY_WINDOW_END": "15:30",
            "FLATTEN_TIME": "15:45",
        }
    )

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import sys
from typing import Callable, Sequence, TextIO

from alpaca_bot.config import Settings, TradingMode
from alpaca_bot.storage import (
    AuditEvent,
    AuditEventStore,
    TradingStatus,
    TradingStatusStore,
    TradingStatusValue,
)
from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres


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

    return parser


def run_admin_command(
    argv: Sequence[str],
    *,
    settings: Settings,
    connection: ConnectionProtocol,
    now: datetime | None = None,
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
        return _write_status_change(
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

    if args.command == "close-only":
        return _write_status_change(
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

    if args.command == "resume":
        return _write_status_change(
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

    raise ValueError(f"Unsupported command: {args.command}")


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
    reason: str,
    now: datetime,
    kill_switch_enabled: bool,
) -> str:
    del connection, settings
    status = TradingStatus(
        trading_mode=trading_mode,
        strategy_version=strategy_version,
        status=new_status,
        kill_switch_enabled=kill_switch_enabled,
        status_reason=reason,
        updated_at=now,
    )
    status_store.save(status)
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
        )
    )
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
) -> int:
    if settings is not None:
        resolved_settings = settings
    elif connect is not None:
        resolved_settings = _fallback_settings()
    else:
        resolved_settings = Settings.from_env()
    connection = connect() if connect is not None else connect_postgres(resolved_settings.database_url)
    try:
        args = build_parser(resolved_settings).parse_args(list(argv or []))
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

            status = TradingStatus(
                trading_mode=trading_mode,
                strategy_version=strategy_version,
                status=status_value,
                kill_switch_enabled=kill_switch_enabled,
                status_reason=command_reason,
                updated_at=timestamp,
            )
            status_store.save(status)
            audit_store.append(
                AuditEvent(
                    event_type="trading_status_changed",
                    payload=_status_change_payload(
                        command_name=args.command,
                        trading_mode=trading_mode,
                        strategy_version=strategy_version,
                        new_status=status_value,
                        reason=command_reason,
                    ),
                    created_at=timestamp,
                )
            )
            output = (
                f"mode={trading_mode.value} "
                f"strategy={strategy_version} "
                f"status={status_value.value} "
                f"reason={command_reason or '-'}"
            )
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()
    print(output, file=stdout or sys.stdout)
    return 0


def _fallback_settings() -> Settings:
    return Settings.from_env(
        {
            "TRADING_MODE": "paper",
            "ENABLE_LIVE_TRADING": "false",
            "STRATEGY_VERSION": "v1-breakout",
            "DATABASE_URL": "postgresql://alpaca_bot:secret@db.example.com:5432/alpaca_bot",
            "MARKET_DATA_FEED": "sip",
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

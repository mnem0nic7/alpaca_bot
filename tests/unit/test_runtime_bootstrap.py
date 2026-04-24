from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from alpaca_bot.config import Settings
from alpaca_bot.runtime import bootstrap_runtime


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self._connection.executed.append((sql, params))

    def fetchone(self) -> object:
        if not self._connection.fetchone_responses:
            return None
        return self._connection.fetchone_responses.pop(0)

    def fetchall(self) -> list[object]:
        if not self._connection.fetchall_responses:
            return []
        return self._connection.fetchall_responses.pop(0)


class FakeConnection:
    def __init__(
        self,
        *,
        fetchone_responses: list[object] | None = None,
        fetchall_responses: list[list[object]] | None = None,
    ) -> None:
        self.fetchone_responses = list(fetchone_responses or [])
        self.fetchall_responses = list(fetchall_responses or [])
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.commit_count = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commit_count += 1

    def close(self) -> None:
        self.closed = True


def make_settings() -> Settings:
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


def write_migration(path: Path, name: str, sql: str) -> None:
    path.joinpath(name).write_text(sql)


def test_bootstrap_runtime_applies_migrations_then_acquires_lock(tmp_path: Path) -> None:
    write_migration(tmp_path, "001_initial.sql", "SELECT 1;")
    connection = FakeConnection(
        fetchall_responses=[[]],
        fetchone_responses=[(True,)],
    )

    context = bootstrap_runtime(
        make_settings(),
        connection=connection,
        migrations_path=tmp_path,
    )

    executed_sql = "\n".join(sql for sql, _ in connection.executed)
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in executed_sql
    assert "SELECT 1;" in executed_sql
    assert "SELECT pg_try_advisory_lock" in executed_sql
    assert context.settings.strategy_version == "v1-breakout"


def test_bootstrap_runtime_raises_when_singleton_lock_unavailable(tmp_path: Path) -> None:
    write_migration(tmp_path, "001_initial.sql", "SELECT 1;")
    connection = FakeConnection(
        fetchall_responses=[[]],
        fetchone_responses=[(False,)],
    )

    with pytest.raises(RuntimeError, match="singleton trader lock"):
        bootstrap_runtime(
            make_settings(),
            connection=connection,
            migrations_path=tmp_path,
        )

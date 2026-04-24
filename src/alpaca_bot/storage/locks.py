from __future__ import annotations

from hashlib import blake2b

from alpaca_bot.config import TradingMode
from alpaca_bot.storage.db import ConnectionProtocol, fetch_one


def advisory_lock_key(*, strategy_version: str, trading_mode: TradingMode) -> int:
    raw = f"{trading_mode.value}:{strategy_version}".encode("utf-8")
    digest = blake2b(raw, digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class PostgresAdvisoryLock:
    def __init__(
        self,
        connection: ConnectionProtocol,
        *,
        strategy_version: str,
        trading_mode: TradingMode,
    ) -> None:
        self._connection = connection
        self._key = advisory_lock_key(
            strategy_version=strategy_version,
            trading_mode=trading_mode,
        )

    @property
    def key(self) -> int:
        return self._key

    def try_acquire(self) -> bool:
        row = fetch_one(
            self._connection,
            "SELECT pg_try_advisory_lock(%s)",
            (self._key,),
        )
        return bool(_first_value(row))

    def release(self) -> bool:
        row = fetch_one(
            self._connection,
            "SELECT pg_advisory_unlock(%s)",
            (self._key,),
        )
        return bool(_first_value(row))


def _first_value(row: object) -> object:
    if isinstance(row, tuple):
        return row[0]
    return row

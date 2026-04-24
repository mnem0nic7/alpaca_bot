from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class CursorProtocol(Protocol):
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> list[Any]: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...

    def commit(self) -> None: ...


def fetch_one(
    connection: ConnectionProtocol,
    sql: str,
    params: Sequence[Any] | None = None,
) -> Any:
    cursor = connection.cursor()
    cursor.execute(sql, params)
    return cursor.fetchone()


def fetch_all(
    connection: ConnectionProtocol,
    sql: str,
    params: Sequence[Any] | None = None,
) -> list[Any]:
    cursor = connection.cursor()
    cursor.execute(sql, params)
    return cursor.fetchall()


def execute(
    connection: ConnectionProtocol,
    sql: str,
    params: Sequence[Any] | None = None,
    *,
    commit: bool = True,
) -> None:
    cursor = connection.cursor()
    cursor.execute(sql, params)
    if commit:
        connection.commit()


def connect_postgres(database_url: str) -> ConnectionProtocol:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "psycopg is required for runtime Postgres access. Install dependencies first."
        ) from exc

    return psycopg.connect(database_url)

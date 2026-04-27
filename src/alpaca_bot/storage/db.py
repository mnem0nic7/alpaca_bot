from __future__ import annotations

from collections.abc import Sequence
import logging
import time
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


class CursorProtocol(Protocol):
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> list[Any]: ...


class ConnectionProtocol(Protocol):
    def cursor(self) -> CursorProtocol: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


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


def connect_postgres_with_retry(
    database_url: str,
    *,
    max_attempts: int = 3,
    _connect_fn: Callable[[str], ConnectionProtocol] | None = None,
    _sleep_fn: Callable[[float], None] | None = None,
) -> ConnectionProtocol:
    """Attempt to connect to Postgres up to *max_attempts* times.

    Waits 2 seconds between attempts.  Raises the last exception if every
    attempt fails.  Callers can inject *_connect_fn* and *_sleep_fn* for
    testing (production uses :func:`connect_postgres` and :func:`time.sleep`).
    """
    connect = _connect_fn if _connect_fn is not None else connect_postgres
    sleeper = _sleep_fn if _sleep_fn is not None else time.sleep

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return connect(database_url)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "connect_postgres_with_retry: attempt %d/%d failed: %s",
                attempt,
                max_attempts,
                exc,
            )
            if attempt < max_attempts:
                sleeper(2)

    raise last_exc  # type: ignore[misc]


def check_connection(connection: ConnectionProtocol) -> bool:
    """Return *True* if *connection* is alive, *False* if it appears dead.

    Probes the connection by executing ``SELECT 1``.  Any exception (TCP
    timeout, server restart, idle-connection culling) is caught and results in
    *False* so callers can decide to reconnect.
    """
    try:
        connection.cursor().execute("SELECT 1")
        try:
            connection.rollback()
        except Exception:
            pass
        return True
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        return False

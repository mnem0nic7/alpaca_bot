from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path

from alpaca_bot.storage.db import ConnectionProtocol, connect_postgres, execute, fetch_all


SCHEMA_MIGRATIONS_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version BIGINT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path
    sql: str


def discover_migrations(migrations_path: str | Path) -> list[Migration]:
    path = Path(migrations_path)
    migrations: list[Migration] = []
    for file in path.glob("*.sql"):
        if not file.is_file():
            continue
        prefix, _, _ = file.stem.partition("_")
        if not prefix.isdigit():
            continue
        migrations.append(
            Migration(
                version=int(prefix),
                path=file,
                sql=file.read_text(),
            )
        )
    return sorted(migrations, key=lambda migration: migration.version)


class MigrationRunner:
    def __init__(self, *, connection: ConnectionProtocol, migrations_path: str | Path) -> None:
        self._connection = connection
        self._migrations_path = Path(migrations_path)

    def ensure_schema_migrations_table(self) -> None:
        execute(self._connection, SCHEMA_MIGRATIONS_BOOTSTRAP_SQL)

    def applied_versions(self) -> set[int]:
        self.ensure_schema_migrations_table()
        rows = fetch_all(
            self._connection,
            "SELECT version FROM schema_migrations ORDER BY version",
        )
        return {int(row[0] if isinstance(row, tuple) else row) for row in rows}

    def pending_migrations(self) -> list[Migration]:
        applied = self.applied_versions()
        return [
            migration
            for migration in discover_migrations(self._migrations_path)
            if migration.version not in applied
        ]

    def apply_all(self) -> list[Migration]:
        applied_versions: list[Migration] = []
        for migration in self.pending_migrations():
            execute(self._connection, migration.sql, commit=False)
            execute(
                self._connection,
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (migration.version,),
                commit=True,
            )
            applied_versions.append(migration)
        return applied_versions


def apply_database_migrations(
    *,
    database_url: str,
    migrations_path: str | Path,
) -> list[Migration]:
    connection = connect_postgres(database_url)
    try:
        runner = MigrationRunner(connection=connection, migrations_path=migrations_path)
        return runner.apply_all()
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alpaca-bot-migrate")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        required=os.getenv("DATABASE_URL") is None,
    )
    parser.add_argument(
        "--migrations-path",
        default=str(Path(__file__).resolve().parents[3] / "migrations"),
    )
    args = parser.parse_args(argv)
    applied = apply_database_migrations(
        database_url=args.database_url,
        migrations_path=args.migrations_path,
    )
    if applied:
        print("\n".join(migration.path.name for migration in applied))
    else:
        print("up-to-date")
    return 0

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from alpaca_bot.storage.migrations import MigrationRunner, discover_migrations, resolve_migrations_path


def normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self._rows: list[tuple[object, ...]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def __iter__(self) -> Iterator[tuple[object, ...]]:
        return iter(self._rows)

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        normalized = normalize_sql(sql)
        self._connection.executed.append((normalized, params))
        if "SELECT version FROM schema_migrations" in normalized:
            self._rows = [(version,) for version in sorted(self._connection.applied_versions)]
            return
        if "INSERT INTO schema_migrations" in normalized and params is not None:
            version = int(params[0])
            if version not in self._connection.applied_versions:
                self._connection.applied_versions.append(version)
                self._connection.applied_versions.sort()
        self._rows = []

    def fetchone(self) -> object:
        if not self._rows:
            return None
        return self._rows.pop(0)

    def fetchall(self) -> list[object]:
        rows = list(self._rows)
        self._rows = []
        return rows


class FakeConnection:
    def __init__(self, *, applied_versions: list[int] | None = None) -> None:
        self.applied_versions = list(applied_versions or [])
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.commit_count = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commit_count += 1


def write_migration(path: Path, name: str, sql: str) -> None:
    path.joinpath(name).write_text(sql, encoding="utf-8")


def statement_index(
    connection: FakeConnection,
    sql_fragment: str,
    *,
    params: tuple[object, ...] | None = None,
) -> int:
    for index, (sql, seen_params) in enumerate(connection.executed):
        if sql_fragment in sql and (params is None or params == seen_params):
            return index
    raise AssertionError(f"Missing statement containing {sql_fragment!r} with params {params!r}")


def test_discover_migrations_returns_numbered_sql_files_in_order(tmp_path: Path) -> None:
    initial_sql = "CREATE TABLE initial_step (id INTEGER);"
    orders_sql = "CREATE TABLE orders_step (id INTEGER);"
    final_sql = "ALTER TABLE orders_step ADD COLUMN symbol TEXT;"

    write_migration(tmp_path, "010_final.sql", final_sql)
    write_migration(tmp_path, "002_add_orders.sql", orders_sql)
    write_migration(tmp_path, "001_initial.sql", initial_sql)
    write_migration(tmp_path, "scratch.sql", "SELECT 999;")
    tmp_path.joinpath("README.md").write_text("ignore me", encoding="utf-8")

    migrations = discover_migrations(tmp_path)

    assert [(migration.version, migration.path.name, migration.sql) for migration in migrations] == [
        (1, "001_initial.sql", initial_sql),
        (2, "002_add_orders.sql", orders_sql),
        (10, "010_final.sql", final_sql),
    ]


def test_migration_runner_applies_only_pending_migrations_in_order(tmp_path: Path) -> None:
    initial_sql = "CREATE TABLE initial_step (id INTEGER);"
    orders_sql = "CREATE TABLE orders_step (id INTEGER);"
    positions_sql = "ALTER TABLE orders_step ADD COLUMN symbol TEXT;"

    write_migration(tmp_path, "001_initial.sql", initial_sql)
    write_migration(tmp_path, "002_orders.sql", orders_sql)
    write_migration(tmp_path, "003_positions.sql", positions_sql)
    connection = FakeConnection(applied_versions=[1])
    runner = MigrationRunner(connection=connection, migrations_path=tmp_path)

    applied = runner.apply_all()

    assert [(migration.version, migration.path.name) for migration in applied] == [
        (2, "002_orders.sql"),
        (3, "003_positions.sql"),
    ]

    executed_sql = [sql for sql, _ in connection.executed]
    assert any("CREATE TABLE IF NOT EXISTS schema_migrations" in sql for sql in executed_sql)
    assert any("SELECT version FROM schema_migrations" in sql for sql in executed_sql)
    assert normalize_sql(initial_sql) not in executed_sql

    orders_index = statement_index(connection, normalize_sql(orders_sql))
    record_orders_index = statement_index(
        connection,
        "INSERT INTO schema_migrations",
        params=(2,),
    )
    positions_index = statement_index(connection, normalize_sql(positions_sql))
    record_positions_index = statement_index(
        connection,
        "INSERT INTO schema_migrations",
        params=(3,),
    )

    assert orders_index < record_orders_index < positions_index < record_positions_index


def test_migration_runner_noops_when_up_to_date(tmp_path: Path) -> None:
    initial_sql = "CREATE TABLE initial_step (id INTEGER);"
    orders_sql = "CREATE TABLE orders_step (id INTEGER);"

    write_migration(tmp_path, "001_initial.sql", initial_sql)
    write_migration(tmp_path, "002_orders.sql", orders_sql)
    connection = FakeConnection(applied_versions=[1, 2])
    runner = MigrationRunner(connection=connection, migrations_path=tmp_path)

    applied = runner.apply_all()

    assert applied == []
    executed_sql = [sql for sql, _ in connection.executed]

    assert any("SELECT version FROM schema_migrations" in sql for sql in executed_sql)
    assert normalize_sql(initial_sql) not in executed_sql
    assert normalize_sql(orders_sql) not in executed_sql
    assert not any("INSERT INTO schema_migrations" in sql for sql in executed_sql)


def test_resolve_migrations_path_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    custom_path = tmp_path / "custom-migrations"
    custom_path.mkdir()
    monkeypatch.setenv("ALPACA_BOT_MIGRATIONS_PATH", str(custom_path))

    resolved = resolve_migrations_path()

    assert resolved == custom_path

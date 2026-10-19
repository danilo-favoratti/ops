"""Apply each pending SQL migration atomically and retain its application time."""

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from duckdb import DuckDBPyConnection


@dataclass(frozen=True)
class Migration:
    migration_id: str
    sql: str


@dataclass(frozen=True)
class MigrationStatus:
    migration_id: str
    applied_at: datetime | None


def load_migrations(directory: Path | None = None) -> tuple[Migration, ...]:
    """Read bundled SQL files or an explicit migration directory in name order."""
    source = directory or files("opsiu.adapters.duckdb").joinpath("migrations")
    paths = sorted(
        (
            path
            for path in source.iterdir()
            if path.is_file() and path.name.endswith(".sql")
        ),
        key=lambda path: path.name,
    )
    return tuple(
        Migration(path.name.removesuffix(".sql"), path.read_text(encoding="utf-8"))
        for path in paths
    )


def _applied_migrations(connection: DuckDBPyConnection) -> dict[str, datetime]:
    exists = connection.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return {}
    rows = connection.execute(
        "SELECT migration_id, applied_at FROM schema_migrations ORDER BY migration_id"
    ).fetchall()
    applied: dict[str, datetime] = {}
    for migration_id, applied_at in rows:
        if not isinstance(migration_id, str) or not isinstance(applied_at, datetime):
            raise TypeError("schema_migrations contains invalid column types")
        applied[migration_id] = applied_at
    return applied


def migration_status(
    connection: DuckDBPyConnection | None,
    *,
    directory: Path | None = None,
) -> tuple[MigrationStatus, ...]:
    """Inspect migration history without creating a database or ledger table."""
    applied = _applied_migrations(connection) if connection is not None else {}
    bundled = {migration.migration_id for migration in load_migrations(directory)}
    return tuple(
        MigrationStatus(migration_id, applied.get(migration_id))
        for migration_id in sorted(bundled | applied.keys())
    )


def apply_migrations(
    connection: DuckDBPyConnection, *, directory: Path | None = None
) -> tuple[str, ...]:
    """Apply pending migrations with SQL and history insertion in one transaction."""
    applied = _applied_migrations(connection)
    newly_applied: list[str] = []
    for migration in load_migrations(directory):
        if migration.migration_id in applied:
            continue
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations (migration_id, applied_at) "
                "VALUES (?, ?)",
                [migration.migration_id, datetime.now(UTC)],
            )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        newly_applied.append(migration.migration_id)
    return tuple(newly_applied)

"""Real DuckDB migration transactions, connection lifetime, and CLI behavior."""

from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.migration_runner import (
    apply_migrations,
    load_migrations,
    migration_status,
)
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration


def test_initial_migration_is_complete_and_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "opsiu.duckdb"
    with connect_database(database) as connection:
        expected = tuple(migration.migration_id for migration in load_migrations())
        assert apply_migrations(connection) == expected
        assert {row[0] for row in connection.execute("SHOW TABLES").fetchall()} >= {
            "schema_migrations",
            "runs",
            "steps",
            "events",
            "source_files",
            "caps",
            "cota_uf",
            "adicionais",
        }
        first_status = migration_status(connection)
        assert all(status.applied_at is not None for status in first_status)
        assert apply_migrations(connection) == ()
        assert migration_status(connection) == first_status


def test_migrations_are_lexical_and_failure_rolls_back(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    initial = load_migrations()[0]
    (migrations / "0001_init.sql").write_text(initial.sql, encoding="utf-8")
    (migrations / "0003_failure.sql").write_text(
        "CREATE TABLE partial_change (id INTEGER); SELECT * FROM missing_table;",
        encoding="utf-8",
    )
    (migrations / "0002_success.sql").write_text(
        "CREATE TABLE completed_change (id INTEGER);", encoding="utf-8"
    )

    with connect_database(tmp_path / "opsiu.duckdb") as connection:
        with pytest.raises(duckdb.CatalogException):
            apply_migrations(connection, directory=migrations)
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert "completed_change" in tables
        assert "partial_change" not in tables
        statuses = migration_status(connection, directory=migrations)
        assert [status.migration_id for status in statuses] == [
            "0001_init",
            "0002_success",
            "0003_failure",
        ]
        assert [status.applied_at is not None for status in statuses] == [
            True,
            True,
            False,
        ]


def test_failed_initial_migration_leaves_no_history(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_broken.sql").write_text(
        load_migrations()[0].sql + "\nSELECT * FROM missing_table;", encoding="utf-8"
    )

    with connect_database(tmp_path / "opsiu.duckdb") as connection:
        with pytest.raises(duckdb.CatalogException):
            apply_migrations(connection, directory=migrations)
        assert connection.execute("SHOW TABLES").fetchall() == []


def test_connections_close_and_read_only_rejects_writes(tmp_path: Path) -> None:
    database = tmp_path / "opsiu.duckdb"
    with connect_database(database) as connection:
        apply_migrations(connection)
    with pytest.raises(duckdb.ConnectionException):
        connection.execute("SELECT 1")

    with connect_database(database, read_only=True) as read_only:
        assert migration_status(read_only)[0].applied_at is not None
        with pytest.raises(duckdb.InvalidInputException):
            read_only.execute("CREATE TABLE forbidden_write (id INTEGER)")


def test_read_only_does_not_create_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "opsiu.duckdb"
    with pytest.raises(duckdb.IOException):
        with connect_database(missing, read_only=True):
            pytest.fail("a missing database cannot be opened read-only")
    assert not missing.parent.exists()


def test_cli_status_and_migrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "opsiu.toml"
    config.write_text('[paths]\ndb_path = "db/opsiu.duckdb"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPSIU_CONFIG", str(config))
    monkeypatch.delenv("OPSIU_PATHS__DB_PATH", raising=False)
    runner = CliRunner()

    before = runner.invoke(app, ["db", "status"])
    assert before.exit_code == 0, before.exception
    assert "0001_init: pendente" in before.stdout
    assert not (tmp_path / "db").exists()

    migrated = runner.invoke(app, ["db", "migrate"])
    assert migrated.exit_code == 0, migrated.exception
    assert "Aplicada: 0001_init" in migrated.stdout
    repeated = runner.invoke(app, ["db", "migrate"])
    assert repeated.exit_code == 0, repeated.exception
    assert "Nenhuma migração pendente" in repeated.stdout
    after = runner.invoke(app, ["db", "status"])
    assert after.exit_code == 0, after.exception
    assert "0001_init: aplicada em" in after.stdout

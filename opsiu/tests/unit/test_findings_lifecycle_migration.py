"""Upgrade real audit history atomically without changing finding identities."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.migration_runner import apply_migrations, load_migrations

pytestmark = pytest.mark.unit
MIGRATION_ID = "0007a_findings_lifecycle"
CREATED = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
SUPERSEDED = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
ACTIVE = "a" * 64
HISTORICAL = "b" * 64
AUDIT_TABLES = (
    "runs",
    "steps",
    "source_files",
    "snapshots",
    "nf_texto",
    "nf_texto_findings",
    "agent_traces",
    "agent_trace_records",
    "pareceres",
    "findings",
)


def identity(number: int) -> str:
    return f"00000000-0000-7000-8000-{number:012d}"


def insert(
    connection: DuckDBPyConnection, table: str, values: dict[str, object]
) -> None:
    connection.execute(
        f"INSERT INTO {table} ({', '.join(values)}) "
        f"VALUES ({', '.join('?' for _ in values)})",
        list(values.values()),
    )


def rows(connection: DuckDBPyConnection, table: str) -> tuple[tuple[object, ...], ...]:
    return tuple(connection.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall())


def seed_history(connection: DuckDBPyConnection) -> None:
    for number, started in ((1, CREATED), (2, SUPERSEDED)):
        insert(
            connection,
            "runs",
            {
                "run_id": identity(number),
                "command": "check",
                "args_json": '{"rules":["R08"]}',
                "settings_hash": "c" * 64,
                "code_commit": "original-commit",
                "code_dirty": False,
                "started_at": started,
                "finished_at": started,
                "status": "completed",
                "counters_json": '{"findings":1}',
            },
        )
    insert(
        connection,
        "steps",
        {
            "step_id": identity(3),
            "run_id": identity(1),
            "name": "check.R08",
            "started_at": CREATED,
            "finished_at": CREATED,
            "status": "completed",
        },
    )
    insert(
        connection,
        "source_files",
        {
            "source_file_id": identity(4),
            "path": "data/Ano-2025.json",
            "sha256": "d" * 64,
            "size": 123,
            "ingested_at": CREATED,
            "run_id": identity(1),
            "source_year": 2025,
        },
    )
    provenance: dict[str, object] = {
        "run_id": identity(1),
        "step_id": identity(3),
    }
    for document, key in ((101, ACTIVE), (102, HISTORICAL)):
        insert(
            connection,
            "findings",
            {
                "finding_key": key,
                "rule_id": "R08",
                "rule_version": "1",
                "id_documento": document,
                "severity": "alta",
                "amount": "123.45",
                "evidence_json": '{"motivo":"Evidência original."}',
                "status": "active" if key == ACTIVE else "superseded",
                **provenance,
                "source_file_id": identity(4),
                "record_sha256": "e" * 64,
                "code_commit": "original-commit",
                "created_at": CREATED,
                "superseded_at": None if key == ACTIVE else SUPERSEDED,
                "superseded_by_run_id": None if key == ACTIVE else identity(2),
            },
        )
    insert(
        connection,
        "snapshots",
        {
            "snapshot_id": identity(5),
            "source": "nota-fiscal",
            "path": "external/original.pdf",
            "manifest_json": '{"original":true}',
            "url": "https://example.invalid/nota.pdf",
            "status": 200,
            "headers_json": '{"content-type":"application/pdf"}',
            "sha256": "f" * 64,
            "size_bytes": 100,
            "fetched_at": CREATED,
            **provenance,
        },
    )
    insert(
        connection,
        "nf_texto",
        {
            "extraction_id": identity(6),
            "id_documento": 101,
            "url": "https://example.invalid/nota.pdf",
            "snapshot_id": identity(5),
            "snapshot_sha256": "f" * 64,
            "path": "external/original.txt",
            "sha256": "0" * 64,
            "size_bytes": 14,
            "extractor": "pypdf",
            "extractor_version": "1",
            "texto": "Nota original.",
            "text_sha256": "0" * 64,
            "needs_ocr": False,
            "page_count": 1,
            "created_at": CREATED,
            **provenance,
        },
    )
    for offset, key in enumerate((ACTIVE, HISTORICAL)):
        insert(
            connection,
            "nf_texto_findings",
            {
                "extraction_id": identity(6),
                "finding_key": key,
                "created_at": CREATED,
                **provenance,
            },
        )
        insert(
            connection,
            "agent_traces",
            {
                "agent_trace_id": identity(10 + offset),
                **provenance,
                "finding_key": key,
                "agent_id": "explicar-achado-R08",
                "agent_version": "1",
                "model": "scripted",
                "prompt_name": "explicar-achado",
                "prompt_version": "1",
                "prompt_sha256": "1" * 64,
                "input_schema": "ParecerRequest",
                "output_schema": "Parecer",
                "instructions": "Preservar as evidências originais.",
                "input_json": '{"evidencia":"original"}',
                "output_json": '{"recomendacao":"revisar"}',
                "usage_json": '{"tokens":3}',
                "failure_json": None,
                "status": "succeeded",
                "started_at": CREATED,
                "finished_at": CREATED,
                "duration_ms": 1.25,
            },
        )
        insert(
            connection,
            "pareceres",
            {
                "parecer_id": identity(20 + offset),
                "finding_key": key,
                "agent_trace_id": identity(10 + offset),
                "agent_id": "explicar-achado-R08",
                "kind": "parecer",
                "prompt_version": "1",
                "model": "scripted",
                "output_json": '{"recomendacao":"revisar"}',
                "created_at": CREATED,
                **provenance,
            },
        )
    # A private chunk can exist before its terminal trace; preserve this too.
    insert(
        connection,
        "agent_trace_records",
        {
            "record_id": identity(30),
            "agent_trace_id": identity(31),
            **provenance,
            "kind": "provider",
            "content": "Registro original.",
            "content_bytes": 18,
            "truncated": False,
            "metadata_json": '{"original":true}',
            "correlation_json": '{"finding_key":"original"}',
            "created_at": CREATED,
        },
    )


@pytest.fixture
def legacy_database(
    tmp_path: Path,
) -> Iterator[tuple[DuckDBPyConnection, Path]]:
    directory = tmp_path / "migrations"
    directory.mkdir()
    legacy = tuple(
        migration
        for migration in load_migrations()
        if migration.migration_id < MIGRATION_ID
    )
    assert legacy[-1].migration_id == "0007_agents"
    for migration in legacy:
        (directory / f"{migration.migration_id}.sql").write_text(
            migration.sql, encoding="utf-8"
        )
    with duckdb.connect() as connection:
        assert apply_migrations(connection, directory=directory) == tuple(
            migration.migration_id for migration in legacy
        )
        seed_history(connection)
        yield connection, directory


def add_upgrade(directory: Path, *, suffix: str = "") -> None:
    migration = next(
        item for item in load_migrations() if item.migration_id == MIGRATION_ID
    )
    (directory / f"{MIGRATION_ID}.sql").write_text(
        migration.sql + suffix, encoding="utf-8"
    )


def test_upgrade_preserves_original_rows_ids_and_supersession_metadata(
    legacy_database: tuple[DuckDBPyConnection, Path],
) -> None:
    connection, directory = legacy_database
    before = {table: rows(connection, table) for table in AUDIT_TABLES}
    public_columns = connection.execute("DESCRIBE findings").fetchall()
    old_history = rows(connection, "schema_migrations")
    add_upgrade(directory)

    assert apply_migrations(connection, directory=directory) == (MIGRATION_ID,)
    assert {table: rows(connection, table) for table in AUDIT_TABLES} == before
    assert [row[:2] for row in connection.execute("DESCRIBE findings").fetchall()] == [
        row[:2] for row in public_columns
    ]
    assert connection.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_name='findings'"
    ).fetchone() == ("VIEW",)
    assert rows(connection, "finding_supersessions") == (
        (HISTORICAL, SUPERSEDED, UUID(identity(2))),
    )
    assert rows(connection, "finding_records") == tuple(
        row[:7] + row[8:14] for row in before["findings"]
    )
    assert rows(connection, "schema_migrations")[:-1] == old_history
    upgraded_history = rows(connection, "schema_migrations")
    assert apply_migrations(connection, directory=directory) == ()
    assert rows(connection, "schema_migrations") == upgraded_history


@pytest.mark.parametrize(
    "table", ["nf_texto_findings", "agent_traces", "pareceres", "finding_supersessions"]
)
def test_upgraded_foreign_keys_reject_unknown_finding_identity(
    legacy_database: tuple[DuckDBPyConnection, Path], table: str
) -> None:
    connection, directory = legacy_database
    add_upgrade(directory)
    apply_migrations(connection, directory=directory)
    # An unannotated, valid trace prevents a uniqueness conflict from masking
    # the foreign-key assertion when adding a new opinion.
    connection.execute(
        "INSERT INTO agent_traces SELECT * REPLACE (? AS agent_trace_id) "
        "FROM agent_traces WHERE finding_key = ?",
        [identity(40), ACTIVE],
    )
    replacements = {
        "nf_texto_findings": "'unknown-finding' AS finding_key",
        "agent_traces": (
            f"'{identity(41)}' AS agent_trace_id, 'unknown-finding' AS finding_key"
        ),
        "pareceres": (
            f"'{identity(42)}' AS parecer_id, '{identity(40)}' AS agent_trace_id, "
            "'unknown-finding' AS finding_key"
        ),
        "finding_supersessions": "'unknown-finding' AS finding_key",
    }
    before = rows(connection, table)
    with pytest.raises(duckdb.ConstraintException, match="unknown-finding"):
        connection.execute(
            f"INSERT INTO {table} SELECT * REPLACE ({replacements[table]}) "
            f"FROM {table} LIMIT 1"
        )
    assert rows(connection, table) == before


def test_late_failure_rolls_back_schema_data_and_history_then_retry_succeeds(
    legacy_database: tuple[DuckDBPyConnection, Path],
) -> None:
    connection, directory = legacy_database
    before = {table: rows(connection, table) for table in AUDIT_TABLES}
    history = rows(connection, "schema_migrations")
    schema_query = (
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = 'main' ORDER BY table_name"
    )
    schema = connection.execute(schema_query).fetchall()
    constraints = connection.execute(
        "SELECT table_name, constraint_type, constraint_text "
        "FROM duckdb_constraints() ORDER BY ALL"
    ).fetchall()
    # Execute every real migration statement, then create a history collision.
    # The runner's final ledger INSERT must fail and roll back this entry too.
    add_upgrade(
        directory,
        suffix=(
            "\nINSERT INTO schema_migrations VALUES "
            f"('{MIGRATION_ID}', TIMESTAMPTZ '2026-01-01 00:00:00+00');\n"
        ),
    )
    with pytest.raises(duckdb.ConstraintException, match=MIGRATION_ID):
        apply_migrations(connection, directory=directory)

    assert connection.execute(schema_query).fetchall() == schema
    assert (
        connection.execute(
            "SELECT table_name, constraint_type, constraint_text "
            "FROM duckdb_constraints() ORDER BY ALL"
        ).fetchall()
        == constraints
    )
    assert {table: rows(connection, table) for table in AUDIT_TABLES} == before
    assert rows(connection, "schema_migrations") == history
    add_upgrade(directory)
    assert apply_migrations(connection, directory=directory) == (MIGRATION_ID,)
    assert {table: rows(connection, table) for table in AUDIT_TABLES} == before
    assert len(rows(connection, "schema_migrations")) == len(history) + 1


@pytest.mark.parametrize("fail", [False, True])
def test_record_identity_upgrade_preserves_all_references_and_rolls_back(
    legacy_database: tuple[DuckDBPyConnection, Path],
    fail: bool,
) -> None:
    connection, directory = legacy_database
    for migration in load_migrations():
        if MIGRATION_ID <= migration.migration_id < "0011_source_record_identity":
            (directory / f"{migration.migration_id}.sql").write_text(migration.sql)
    apply_migrations(connection, directory=directory)
    insert(
        connection,
        "decisions",
        {
            "decision_id": identity(40),
            "finding_key": ACTIVE,
            "decision": "aprovado",
            "reviewer": "Revisora",
            "comment": "Preservar",
            "decided_at": CREATED,
            "run_id": identity(1),
            "step_id": identity(3),
        },
    )
    insert(
        connection,
        "resumos_deputado",
        {
            "summary_id": identity(41),
            "numero_deputado_id": 7,
            "nome_parlamentar": "Ana",
            "agent_trace_id": identity(10),
            "model": "scripted",
            "prompt_name": "resumir",
            "prompt_version": "1",
            "prompt_sha256": "1" * 64,
            "input_sha256": "2" * 64,
            "input_json": "{}",
            "output_json": "{}",
            "created_at": CREATED,
            "run_id": identity(1),
            "step_id": identity(3),
        },
    )
    insert(
        connection,
        "resumo_fontes",
        {
            "summary_id": identity(41),
            "finding_key": ACTIVE,
            "parecer_id": identity(20),
            "parecer_trace_id": identity(10),
            "source_file_id": identity(4),
            "source_sha256": "d" * 64,
            "record_sha256": "e" * 64,
        },
    )
    tables = (
        *AUDIT_TABLES,
        "decisions",
        "resumos_deputado",
        "resumo_fontes",
        "finding_records",
        "finding_supersessions",
        "findings_current",
    )
    before = {table: rows(connection, table) for table in tables}
    columns = {
        table: [row[0] for row in connection.execute(f"DESCRIBE {table}").fetchall()]
        for table in tables
    }
    migration = next(
        item
        for item in load_migrations()
        if item.migration_id == "0011_source_record_identity"
    )
    (directory / f"{migration.migration_id}.sql").write_text(
        migration.sql + ("\nSELECT * FROM intentional_missing_table;" if fail else "")
    )
    if fail:
        with pytest.raises(duckdb.Error):
            apply_migrations(connection, directory=directory)
        assert all(rows(connection, table) == before[table] for table in tables)
    else:
        assert apply_migrations(connection, directory=directory) == (
            migration.migration_id,
        )
        for table in tables:
            assert (
                tuple(
                    connection.execute(
                        f"SELECT {', '.join(columns[table])} FROM {table} ORDER BY ALL"
                    ).fetchall()
                )
                == before[table]
            )
        assert connection.execute(
            "SELECT DISTINCT record_identity FROM findings"
        ).fetchall() == [("",)]
        assert apply_migrations(connection, directory=directory) == ()
    with pytest.raises(duckdb.ConstraintException):
        insert(
            connection,
            "decisions",
            {
                "decision_id": identity(42),
                "finding_key": "z" * 64,
                "decision": "revisar",
                "reviewer": "Revisora",
                "comment": "",
                "decided_at": CREATED,
                "run_id": identity(1),
                "step_id": identity(3),
            },
        )

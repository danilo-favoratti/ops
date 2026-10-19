"""Upgrade historical annotated findings without rewriting their audit references."""

import io
import json
import os
import threading
from collections.abc import Iterator
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from duckdb import DuckDBPyConnection
from pypdf import PdfWriter
from typer.testing import CliRunner, Result

from opsiu.adapters.duckdb import migration_runner
from opsiu.adapters.duckdb.check_repository import DuckDBCheckRepository
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.application.ports import FindingChanges
from opsiu.domain.findings import Finding, evidence_json
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"
CORRECTION = "0007a_findings_lifecycle"
REFERENCE_TABLES = (
    "source_files",
    "despesas",
    "snapshots",
    "nf_texto",
    "nf_texto_findings",
    "agent_traces",
    "agent_trace_records",
    "pareceres",
)


@pytest.fixture
def pdf_url() -> Iterator[str]:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    buffer = io.BytesIO()
    writer.write(buffer)
    payload = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/invoice.pdf"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def invoke(*arguments: str, expected: int = 0) -> Result:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.exception or result.output
    return result


def rows(connection: DuckDBPyConnection, table: str) -> list[tuple[object, ...]]:
    return connection.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()


def finding(connection: DuckDBPyConnection) -> dict[str, object]:
    cursor = connection.execute("SELECT * FROM findings")
    names = [column[0] for column in cursor.description]
    values = cursor.fetchall()
    assert len(values) == 1
    return dict(zip(names, values[0], strict=True))


def legacy_publish(
    self: DuckDBCheckRepository,
    rule_id: str,
    rule_version: str,
    findings: tuple[Finding, ...],
    *,
    run_id: str,
    step_id: str,
    finished_at: datetime,
) -> FindingChanges:
    """Exercise the historical writes against the genuine historical schema."""
    self.connection.execute("BEGIN TRANSACTION")
    try:
        for item in findings:
            self.connection.execute(
                "INSERT INTO findings (finding_key, rule_id, rule_version, "
                "id_documento, severity, amount, evidence_json, status, run_id, "
                "step_id, source_file_id, record_sha256, code_commit, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
                [
                    item.finding_key,
                    rule_id,
                    rule_version,
                    item.id_documento,
                    item.severity,
                    item.amount,
                    evidence_json(item.evidence),
                    run_id,
                    step_id,
                    item.provenance.source.source_file_id,
                    item.provenance.source.record_sha256,
                    item.provenance.code_commit,
                    item.provenance.created_at,
                ],
            )
        if not findings:
            self.connection.execute(
                "UPDATE findings SET status='superseded', superseded_at=?, "
                "superseded_by_run_id=? WHERE rule_id=? AND status='active'",
                [finished_at, run_id, rule_id],
            )
        self.connection.execute("COMMIT")
    except BaseException:
        self.connection.execute("ROLLBACK")
        raise
    return FindingChanges(len(findings), 0, 0)


def test_upgrade_allows_supersession_and_preserves_annotation_and_pdf_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pdf_url: str,
) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    all_migrations = migration_runner.load_migrations()
    correction = next(
        item for item in all_migrations if item.migration_id == CORRECTION
    )
    available = [item for item in all_migrations if item.migration_id <= "0007_agents"]
    assert available[-1].migration_id == "0007_agents"

    def historical_migrations(
        directory: Path | None = None,
    ) -> tuple[migration_runner.Migration, ...]:
        assert directory is None
        return tuple(available)

    monkeypatch.setattr(migration_runner, "load_migrations", historical_migrations)
    current_publish = DuckDBCheckRepository.publish_findings
    monkeypatch.setattr(DuckDBCheckRepository, "publish_findings", legacy_publish)
    (tmp_path / "input").mkdir()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2099\nto=2099\n',
        encoding="utf-8",
    )
    expense = json.loads(FIXTURE.read_text(encoding="utf-8"))["dados"][39]
    expense["urlDocumento"] = pdf_url
    source = tmp_path / "input/Ano-2099.json"
    source.write_text(json.dumps({"dados": [expense]}), encoding="utf-8")
    invoke("ingest")
    invoke("check", "--regras", "R08")
    invoke("fetch", "pdf", "--sample")
    invoke("explain", "--regras", "R08")
    with connect_database(tmp_path / "db.duckdb", read_only=True) as connection:
        original = finding(connection)
        assert original["status"] == "active"
        assert rows(connection, "nf_texto_findings")
        assert len(rows(connection, "pareceres")) == 1
        assert len(rows(connection, "agent_traces")) == 1
        assert connection.execute(
            "SELECT migration_id FROM schema_migrations ORDER BY migration_id DESC "
            "LIMIT 1"
        ).fetchone() == ("0007_agents",)

    # This is the historical defect, with no consolidation migration present.
    source.write_text('{"dados": []}', encoding="utf-8")
    invoke("ingest")
    failed = invoke("check", "--regras", "R08", expected=1)
    failed_run = failed.stdout.splitlines()[0].removeprefix("run_id: ")
    with connect_database(tmp_path / "db.duckdb", read_only=True) as connection:
        assert finding(connection) == original
        errors = connection.execute(
            "SELECT attributes_json FROM events WHERE run_id=? AND kind='step.failed'",
            [failed_run],
        ).fetchall()
        assert any("foreign key constraint" in str(row[0]).lower() for row in errors)
        references = {table: rows(connection, table) for table in REFERENCE_TABLES}
        prior_migrations = rows(connection, "schema_migrations")

    # Upgrade the actual old rows and restore the production publisher.
    available.append(correction)
    invoke("db", "migrate")
    monkeypatch.setattr(DuckDBCheckRepository, "publish_findings", current_publish)
    with connect_database(tmp_path / "db.duckdb") as connection:
        assert finding(connection) == original
        assert all(
            rows(connection, table) == data for table, data in references.items()
        )
        migrated = rows(connection, "schema_migrations")
        assert len(migrated) == len(prior_migrations) + 1
        assert all(row in migrated for row in prior_migrations)
        assert migration_runner.apply_migrations(connection) == ()
        assert rows(connection, "schema_migrations") == migrated
        assert connection.execute(
            "SELECT table_type FROM information_schema.tables "
            "WHERE table_schema='main' AND table_name='findings'"
        ).fetchone() == ("VIEW",)
        immutable = rows(connection, "finding_records")
        assert len(immutable) == 1
        assert rows(connection, "finding_supersessions") == []

    checked = invoke("check", "--regras", "R08")
    checked_run = checked.stdout.splitlines()[0].removeprefix("run_id: ")
    with connect_database(tmp_path / "db.duckdb", read_only=True) as connection:
        superseded = finding(connection)
        assert superseded["status"] == "superseded"
        assert superseded["superseded_at"] is not None
        assert str(superseded["superseded_by_run_id"]) == checked_run
        for key in original.keys() - {
            "status",
            "superseded_at",
            "superseded_by_run_id",
        }:
            assert superseded[key] == original[key]
        assert all(
            rows(connection, table) == data for table, data in references.items()
        )
        assert rows(connection, "schema_migrations") == migrated
        assert rows(connection, "finding_records") == immutable
        assert rows(connection, "finding_supersessions") == [
            (
                original["finding_key"],
                superseded["superseded_at"],
                superseded["superseded_by_run_id"],
            )
        ]

    invoke("check", "--regras", "R08")
    with connect_database(tmp_path / "db.duckdb", read_only=True) as connection:
        assert finding(connection) == superseded
        assert rows(connection, "finding_records") == immutable
        assert len(rows(connection, "finding_supersessions")) == 1
        assert all(
            rows(connection, table) == data for table, data in references.items()
        )

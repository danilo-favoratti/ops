"""Progress survives failed normalization after a full batch was staged."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.cnpj_repository import DuckDBCnpjRepository
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.external.cnpj_snapshots import LocalCnpjSnapshotStore
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.fetch import fetch_cnpj
from opsiu.application.ports import (
    CnpjCompany,
    CnpjOrigin,
    CnpjProgress,
    CnpjRecord,
    CnpjSnapshot,
)
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "cnpj"


class RequestedRootRepository(DuckDBCnpjRepository):
    def requested_roots(self) -> frozenset[str]:
        return frozenset({"00000001"})


class FailingSource:
    def records(
        self, snapshot: CnpjSnapshot, roots: frozenset[str]
    ) -> Iterator[CnpjRecord | CnpjProgress]:
        file = next(
            file for file in snapshot.manifest.files if file.family == "empresas"
        )
        for ordinal in range(1, 10_002):
            yield CnpjCompany(
                "00000001",
                "EMPRESA",
                "2062",
                CnpjOrigin(file.name, file.sha256, file.name, ordinal),
            )
        yield CnpjProgress("empresas", file.name, file.name, 1_000_000)
        raise DomainError("late parse failure after a million scanned rows")


def test_progress_and_selected_snapshot_survive_late_parser_failure(
    tmp_path: Path,
) -> None:
    with connect_database(tmp_path / "cnpj.duckdb") as connection:
        apply_migrations(connection)
        run = Run(
            ledger=CompositeLedger(
                JsonlLedger(tmp_path / "runs"), DuckDBLedger(connection)
            ),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="fetch cnpj test",
            arguments={},
            metadata=collect_metadata("settings", tmp_path),
        )
        with run:
            fetch_cnpj(
                run,
                LocalCnpjSnapshotStore(tmp_path / "external", connection, run),
                FailingSource(),
                RequestedRootRepository(connection),
                directory=FIXTURE,
            )
        assert run.status == "completed_with_errors"
        assert connection.execute("SELECT count(*) FROM cnpj_imports").fetchone() == (
            0,
        )
        assert connection.execute("SELECT count(*) FROM cnpj_empresas").fetchone() == (
            0,
        )
        assert not any(
            str(row[0]).endswith("_stage")
            for row in connection.execute("SHOW TABLES").fetchall()
        )
        sql_events = DuckDBLedger(connection).get_events(run.run_id)
        progress = next(event for event in sql_events if event.kind == "cnpj.progress")
        assert progress.attributes["scanned_rows"] == 1_000_000
        file_events = [
            json.loads(line)
            for line in (tmp_path / "runs" / run.run_id / "events.jsonl")
            .read_text()
            .splitlines()
        ]
        persisted = next(
            event for event in file_events if event["event_id"] == progress.event_id
        )
        assert persisted["attributes"] == progress.attributes
        selected = next(
            event for event in sql_events if event.kind == "cnpj.snapshot_selected"
        )
        manifest = json.loads(
            (tmp_path / "runs" / run.run_id / "manifest.json").read_text()
        )
        assert manifest["cnpj_selected_snapshot"] == selected.attributes

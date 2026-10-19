"""Candidate deduplication and rollback after a full staged batch."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.tse_repository import DuckDBTseRepository
from opsiu.adapters.external.snapshots import HttpSnapshotStore
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.fetch import fetch_tse
from opsiu.application.ports import Snapshot, TseCandidate
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.integration


class ScriptedSource:
    def __init__(self, *, count: int = 1, fail: bool = False) -> None:
        self.count = count
        self.fail = fail

    def candidates(self, snapshot: Snapshot, year: int) -> Iterator[TseCandidate]:
        for number in range(1, self.count + 1):
            yield TseCandidate(
                str(number),
                year,
                None,
                "JOSE DA SILVA",
                "SP",
                "PREFEITO",
                "csv",
                number,
            )
        if self.fail:
            raise DomainError("late candidate parse failure")


def build_run(connection: DuckDBPyConnection, tmp_path: Path) -> Run:
    return Run(
        ledger=CompositeLedger(
            JsonlLedger(tmp_path / "runs"), DuckDBLedger(connection)
        ),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="fetch tse test",
        arguments={},
        metadata=collect_metadata("settings", tmp_path),
    )


def test_late_parse_failure_rolls_back_staged_candidates_and_preserves_current(
    tmp_path: Path,
) -> None:
    with (
        connect_database(tmp_path / "tse.duckdb") as connection,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, stream=httpx.ByteStream(request.url.path.encode())
                )
            )
        ) as client,
    ):
        apply_migrations(connection)
        repository = DuckDBTseRepository(connection)
        initial = build_run(connection, tmp_path)
        with initial:
            fetch_tse(
                initial,
                HttpSnapshotStore(
                    tmp_path / "external", connection, initial, client=client
                ),
                ScriptedSource(),
                repository,
                year=2024,
                url="https://example.invalid/first",
            )
        previous = repository.current_import(2024)
        failed = build_run(connection, tmp_path)
        with failed:
            fetch_tse(
                failed,
                HttpSnapshotStore(
                    tmp_path / "external", connection, failed, client=client
                ),
                ScriptedSource(count=10_001, fail=True),
                repository,
                year=2024,
                url="https://example.invalid/changed",
            )
        assert failed.status == "completed_with_errors"
        assert repository.current_import(2024) == previous
        assert connection.execute("SELECT count(*) FROM tse_candidatos").fetchone() == (
            1,
        )
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM events WHERE run_id = ? AND kind = 'step.failed'",
            [failed.run_id],
        ).fetchone() == (1,)


def test_repeated_candidate_ids_deduplicate_but_conflicting_metadata_rolls_back(
    tmp_path: Path,
) -> None:
    with (
        connect_database(tmp_path / "tse.duckdb") as connection,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=httpx.ByteStream(b"csv"))
            )
        ) as client,
    ):
        apply_migrations(connection)
        repository = DuckDBTseRepository(connection)
        run = build_run(connection, tmp_path)
        with run:
            with run.step("duplicates"):
                snapshot = HttpSnapshotStore(
                    tmp_path / "external", connection, run, client=client
                ).download("tse_2024", "https://example.invalid/data")
                first = next(ScriptedSource().candidates(snapshot, 2024))
                repeated = replace(first, source_record_number=2)
                with repository.stage(
                    import_id=run.ids.new_id(),
                    snapshot=snapshot,
                    year=2024,
                    run_id=run.run_id,
                    step_id=run.require_step(),
                ) as stage:
                    stage.add_batch((first, repeated))
                    imported = stage.publish(loaded_at=run.clock.now())
                assert imported.records_count == 1
            with run.step("conflict"):
                with pytest.raises(DomainError, match="conflicting identity"):
                    with repository.stage(
                        import_id=run.ids.new_id(),
                        snapshot=snapshot,
                        year=2024,
                        run_id=run.run_id,
                        step_id=run.require_step(),
                    ) as stage:
                        stage.add_batch(
                            (first, replace(repeated, nome_normalizado="ANA"))
                        )
                        stage.publish(loaded_at=run.clock.now())
        assert repository.current_import(2024) == imported
        assert connection.execute(
            "SELECT count(*), min(source_record_number) FROM tse_candidatos"
        ).fetchone() == (1, 1)

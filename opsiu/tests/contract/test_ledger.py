"""Ledger sinks preserve event identity, manifests, and atomic bootstrap replay."""

import json
from pathlib import Path

import pytest

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import EventRecord, JsonValue
from opsiu.application.runs import Run

pytestmark = pytest.mark.contract


def test_failure_events_match_both_sinks_and_manifest_records_metadata(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = JsonlLedger(tmp_path / "runs")
    with connect_database(tmp_path / "db.duckdb") as connection:
        apply_migrations(connection)
        database = DuckDBLedger(connection)
        run = Run(
            ledger=CompositeLedger(files, database),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="contract",
            arguments={"input": "fixture"},
            metadata=collect_metadata("hash", tmp_path),
        )
        with run:
            with run.step("failure"):
                raise ValueError("invalid evidence")
            with run.step("continues"):
                run.counters["items"] = 1
                run.metadata["source_file_hashes"] = {"fixture.json": "source-hash"}
                run.metadata["model"] = "scripted"

        assert database.get_events(run.run_id) == files.get_events(run.run_id)
        stored = database.get_run(run.run_id)
        assert stored is not None and stored.status == "completed_with_errors"
        assert [step.status for step in database.get_steps(run.run_id)] == [
            "failed",
            "completed",
        ]

    manifest = json.loads((files.run_dir(run.run_id) / "manifest.json").read_text())
    assert manifest["source_file_hashes"] == {"fixture.json": "source-hash"}
    assert manifest["counters"] == {"items": 1}
    assert manifest["model"] == "scripted"
    assert manifest["code_commit"] == "unknown"
    assert manifest["python_version"]
    assert manifest["package_version"] == "0.1.0"
    reloaded = files.get_run(run.run_id)
    assert reloaded is not None and reloaded.extra["model"] == "scripted"
    assert (
        files.run_dir(run.run_id) / "log.jsonl"
    ).read_text() == capsys.readouterr().err


def test_deferred_replay_snapshots_mutable_attributes(tmp_path: Path) -> None:
    files = JsonlLedger(tmp_path / "runs")
    database = DuckDBLedger()
    with connect_database(tmp_path / "db.duckdb") as connection:
        apply_migrations(connection)
        run = Run(
            ledger=CompositeLedger(files, database),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="deferred",
            arguments={},
            metadata=collect_metadata("hash", tmp_path),
        )
        with run:
            attributes: dict[str, JsonValue] = {"value": {"original": True}}
            run.emit("before.attach", attributes)
            attributes["value"] = "mutated"
            database.attach(connection)
            run.emit("after.attach")
        events = database.get_events(run.run_id)
        assert events == files.get_events(run.run_id)
        original = next(event for event in events if event.kind == "before.attach")
        assert original.attributes == {"value": {"original": True}}


def test_failed_attach_rolls_back_database_and_keeps_file_run(tmp_path: Path) -> None:
    files = JsonlLedger(tmp_path / "runs")
    database = DuckDBLedger()
    clock = SystemClock()
    ids = Uuid7Generator()
    with connect_database(tmp_path / "db.duckdb") as connection:
        apply_migrations(connection)
        run = Run(
            ledger=CompositeLedger(files, database),
            clock=clock,
            ids=ids,
            command="failed-attach",
            arguments={},
            metadata=collect_metadata("hash", tmp_path),
        )
        with run:
            with run.step("attach"):
                database.emit(
                    EventRecord(
                        ids.new_id(),
                        run.run_id,
                        ids.new_id(),
                        "invalid.reference",
                        {},
                        clock.now(),
                    )
                )
                database.attach(connection)
        assert database.connection is None
        assert connection.execute("SELECT count(*) FROM runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM steps").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM events").fetchone() == (0,)
    saved = files.get_run(run.run_id)
    assert saved is not None and saved.status == "completed_with_errors"
    assert any(event.kind == "step.failed" for event in files.get_events(run.run_id))


def test_reused_run_id_cannot_change_existing_artifacts(tmp_path: Path) -> None:
    files = JsonlLedger(tmp_path / "runs")
    ids = Uuid7Generator()
    run = Run(
        ledger=files,
        clock=SystemClock(),
        ids=ids,
        command="first",
        arguments={},
        metadata=collect_metadata("hash", tmp_path),
    )
    with run:
        run.emit("original")
    directory = files.run_dir(run.run_id)
    original = {path.name: path.read_bytes() for path in directory.iterdir()}

    class ReusedId:
        def new_id(self) -> str:
            return run.run_id

    repeated = Run(
        ledger=files,
        clock=SystemClock(),
        ids=ReusedId(),
        command="reused",
        arguments={},
        metadata=collect_metadata("hash", tmp_path),
    )
    with pytest.raises(FileExistsError):
        with repeated:
            pytest.fail("the existing run must not be reopened")
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == original


def test_composite_id_collision_preserves_completed_database_run(
    tmp_path: Path,
) -> None:
    files = JsonlLedger(tmp_path / "runs")
    with connect_database(tmp_path / "db.duckdb") as connection:
        apply_migrations(connection)
        database = DuckDBLedger(connection)
        composite = CompositeLedger(files, database)
        run = Run(
            ledger=composite,
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="original",
            arguments={},
            metadata=collect_metadata("hash", tmp_path),
        )
        with run:
            with run.step("complete"):
                run.counters["items"] = 5
        directory = files.run_dir(run.run_id)
        original_files = {path.name: path.read_bytes() for path in directory.iterdir()}
        original_run = database.get_run(run.run_id)
        original_events = database.get_events(run.run_id)
        original_steps = database.get_steps(run.run_id)

        class ReusedId:
            def new_id(self) -> str:
                return run.run_id

        repeated = Run(
            ledger=composite,
            clock=SystemClock(),
            ids=ReusedId(),
            command="collision",
            arguments={},
            metadata=collect_metadata("other", tmp_path),
        )
        with pytest.raises(FileExistsError):
            with repeated:
                pytest.fail("a repeated run identifier must not be accepted")
        late_event = EventRecord(
            Uuid7Generator().new_id(), run.run_id, None, "late", {}, SystemClock().now()
        )
        with pytest.raises(RuntimeError):
            composite.emit(late_event)
        assert database.get_run(run.run_id) == original_run
        assert database.get_steps(run.run_id) == original_steps
        assert database.get_events(run.run_id) == original_events
        assert {
            path.name: path.read_bytes() for path in directory.iterdir()
        } == original_files

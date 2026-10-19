"""Snapshot publication boundaries and deterministic, verified local replay."""

import gzip
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.external.snapshots import HttpSnapshotStore
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import Uuid7Generator, collect_metadata
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.integration


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[DuckDBPyConnection]:
    with connect_database(tmp_path / "snapshots.duckdb") as connection:
        apply_migrations(connection)
        yield connection


def build_run(connection: DuckDBPyConnection, tmp_path: Path) -> Run:
    return Run(
        ledger=CompositeLedger(
            JsonlLedger(tmp_path / "runs"), DuckDBLedger(connection)
        ),
        clock=FixedClock(),
        ids=Uuid7Generator(),
        command="test snapshots",
        arguments={},
        metadata=collect_metadata("settings-hash", tmp_path),
    )


def test_raw_encoded_payload_empty_response_and_tie_order_are_replayable(
    database: DuckDBPyConnection, tmp_path: Path
) -> None:
    compressed = gzip.compress("dados,ação\n1,teste\n".encode(), mtime=0)

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        if request.url.path == "/empty":
            return httpx.Response(204, stream=httpx.ByteStream(b""))
        return httpx.Response(
            200,
            headers={
                "content-encoding": "gzip",
                "content-length": str(len(compressed)),
            },
            stream=httpx.ByteStream(compressed),
        )

    run = build_run(database, tmp_path)
    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        store = HttpSnapshotStore(tmp_path / "external", database, run, client=client)
        with run:
            with run.step("download"):
                first = store.download("tse", "https://example.invalid/data")
                second = store.download("tse", "https://example.invalid/empty")
        assert not client.is_closed
    assert first.path.read_bytes() == compressed
    assert first.manifest.sha256 == hashlib.sha256(compressed).hexdigest()
    assert second.manifest.size_bytes == 0
    assert second.path.read_bytes() == b""
    assert first.manifest.fetched_at == second.manifest.fetched_at
    assert second.snapshot_id > first.snapshot_id
    assert store.list_snapshots("tse") == (second, first)
    assert store.load_latest("tse") == second
    assert store.load_latest("absent") is None
    assert store.list_snapshots("absent") == ()
    with pytest.raises(LookupError, match="snapshot not found"):
        store.load("absent", first.snapshot_id)
    assert run.counters == {
        "snapshots_downloaded": 2,
        "snapshot_bytes": len(compressed),
    }
    saved_run = json.loads(
        (tmp_path / "runs" / run.run_id / "manifest.json").read_text()
    )
    assert len(saved_run["snapshots"]) == 2
    events = DuckDBLedger(database).get_events(run.run_id)
    assert [event.kind for event in events].count("snapshot.downloaded") == 2


def test_database_publication_failure_removes_only_unpublished_files(
    database: DuckDBPyConnection, tmp_path: Path
) -> None:
    run = build_run(database, tmp_path)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(b"original"))
        )
    ) as client:
        store = HttpSnapshotStore(tmp_path / "external", database, run, client=client)
        with run:
            with run.step("first"):
                first = store.download("tse", "https://example.invalid/one")
                original_manifest = (first.path.parent / "manifest.json").read_bytes()
                database.execute(
                    "CREATE UNIQUE INDEX test_one_snapshot_per_source "
                    "ON snapshots(source)"
                )
            with run.step("failed publication"):
                with pytest.raises(duckdb.ConstraintException):
                    store.download("tse", "https://example.invalid/two")
    assert store.list_snapshots("tse") == (first,)
    assert first.path.read_bytes() == b"original"
    assert (first.path.parent / "manifest.json").read_bytes() == original_manifest
    assert list((tmp_path / "external" / "tse").iterdir()) == [first.path.parent]
    assert database.execute("SELECT count(*) FROM snapshots").fetchone() == (1,)
    assert run.counters["snapshots_downloaded"] == 1


@pytest.mark.parametrize("corruption", ["manifest", "missing", "indexed_hash"])
def test_local_replay_rejects_changed_metadata_and_missing_payloads(
    database: DuckDBPyConnection, tmp_path: Path, corruption: str
) -> None:
    run = build_run(database, tmp_path)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(b"payload"))
        )
    ) as client:
        store = HttpSnapshotStore(tmp_path / "external", database, run, client=client)
        with run:
            with run.step("download"):
                snapshot = store.download("tse", "https://example.invalid/data")
    if corruption == "manifest":
        manifest_path = snapshot.path.parent / "manifest.json"
        document = json.loads(manifest_path.read_text())
        document["url"] = "https://example.invalid/changed"
        manifest_path.write_text(json.dumps(document))
    elif corruption == "missing":
        snapshot.path.unlink()
    else:
        database.execute("UPDATE snapshots SET sha256 = ?", ["0" * 64])
    with pytest.raises(DomainError):
        store.load_latest("tse")
    with pytest.raises(DomainError):
        store.list_snapshots("tse")

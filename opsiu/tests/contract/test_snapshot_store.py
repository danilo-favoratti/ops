"""Snapshot boundaries, path validation, and public HTTP metadata contracts."""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.external.snapshots import HttpSnapshotStore
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.runs import Run

pytestmark = pytest.mark.contract

SNAPSHOT_ID = "01900000-0000-7000-8000-0000000000ab"
URL = "https://example.invalid/fixture.json?year=2099"
PAYLOAD = b'{"synthetic":true}\n'


@dataclass
class SnapshotContext:
    connection: DuckDBPyConnection
    run: Run
    files: JsonlLedger
    external_dir: Path


@pytest.fixture
def snapshot_context(tmp_path: Path) -> Iterator[SnapshotContext]:
    files = JsonlLedger(tmp_path / "runs")
    with connect_database(tmp_path / "opsiu.duckdb") as connection:
        apply_migrations(connection)
        run = Run(
            ledger=CompositeLedger(files, DuckDBLedger(connection)),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="snapshot-contract",
            arguments={},
            metadata=collect_metadata("settings-hash", tmp_path),
        )
        yield SnapshotContext(connection, run, files, tmp_path / "external")


@pytest.fixture
def recording_client() -> Iterator[tuple[httpx.Client, list[httpx.Request]]]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(PAYLOAD))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        yield client, requests


def test_download_requires_an_active_run_and_step(
    snapshot_context: SnapshotContext,
    recording_client: tuple[httpx.Client, list[httpx.Request]],
) -> None:
    context = snapshot_context
    client, requests = recording_client
    store = HttpSnapshotStore(
        context.external_dir, context.connection, context.run, client=client
    )
    assert not context.external_dir.exists()

    with pytest.raises(RuntimeError):
        store.download("tse", URL)
    with context.run:
        with pytest.raises(RuntimeError):
            store.download("tse", URL)
    with pytest.raises(RuntimeError):
        store.download("tse", URL)

    assert requests == []
    assert not context.external_dir.exists()
    assert context.connection.execute("SELECT count(*) FROM snapshots").fetchone() == (
        0,
    )


@pytest.mark.parametrize(
    "source",
    [
        "",
        ".",
        "..",
        "../escape",
        "/absolute",
        "TSE",
        "a/b",
        "a\\b",
        "a.b",
        "a" * 65,
        "é",
        "a\n",
        "-source",
        "1source",
    ],
)
def test_invalid_sources_cannot_create_or_read_paths(
    snapshot_context: SnapshotContext,
    recording_client: tuple[httpx.Client, list[httpx.Request]],
    source: str,
) -> None:
    context = snapshot_context
    client, requests = recording_client
    store = HttpSnapshotStore(
        context.external_dir, context.connection, context.run, client=client
    )
    with context.run:
        with context.run.step("validate_source"):
            with pytest.raises(ValueError):
                store.download(source, URL)
    with pytest.raises(ValueError):
        store.list_snapshots(source)
    with pytest.raises(ValueError):
        store.load_latest(source)
    with pytest.raises(ValueError):
        store.load(source, SNAPSHOT_ID)

    assert requests == []
    assert not context.external_dir.exists()


@pytest.mark.parametrize("source", ["a", "cnpj_2099-01", "a" * 64])
def test_valid_source_names_allow_empty_read_only_history(
    snapshot_context: SnapshotContext,
    recording_client: tuple[httpx.Client, list[httpx.Request]],
    source: str,
) -> None:
    context = snapshot_context
    client, requests = recording_client
    store = HttpSnapshotStore(
        context.external_dir, context.connection, context.run, client=client
    )

    assert store.list_snapshots(source) == ()
    assert store.load_latest(source) is None
    with pytest.raises(LookupError):
        store.load(source, SNAPSHOT_ID)
    assert requests == []
    assert not context.external_dir.exists()


@pytest.mark.parametrize(
    "snapshot_id",
    [
        "",
        "../escape",
        "/absolute",
        "not-a-uuid",
        SNAPSHOT_ID.replace("-", ""),
        SNAPSHOT_ID.upper(),
        "{" + SNAPSHOT_ID + "}",
        "../" + SNAPSHOT_ID,
    ],
)
def test_snapshot_identifiers_must_be_canonical_uuid_paths(
    snapshot_context: SnapshotContext,
    recording_client: tuple[httpx.Client, list[httpx.Request]],
    snapshot_id: str,
) -> None:
    context = snapshot_context
    client, requests = recording_client
    store = HttpSnapshotStore(
        context.external_dir, context.connection, context.run, client=client
    )

    with pytest.raises(ValueError):
        store.load("tse", snapshot_id)

    assert requests == []
    assert not context.external_dir.exists()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "//example.invalid/fixture",
        "ftp://example.invalid/fixture",
        "file:///tmp/fixture",
        "data:text/plain,fixture",
        "https://user:secret@example.invalid/fixture",
        "http://user@example.invalid/fixture",
        "https://:secret@example.invalid/fixture",
        "https://",
    ],
)
def test_invalid_urls_are_rejected_before_external_side_effects(
    snapshot_context: SnapshotContext,
    recording_client: tuple[httpx.Client, list[httpx.Request]],
    url: str,
) -> None:
    context = snapshot_context
    client, requests = recording_client
    store = HttpSnapshotStore(
        context.external_dir, context.connection, context.run, client=client
    )
    with context.run:
        with context.run.step("validate_url"):
            with pytest.raises((ValueError, httpx.InvalidURL)):
                store.download("tse", url)

    assert requests == []
    assert not context.external_dir.exists()
    assert context.connection.execute("SELECT count(*) FROM snapshots").fetchone() == (
        0,
    )


def test_manifest_records_public_headers_and_complete_origin(
    snapshot_context: SnapshotContext,
) -> None:
    context = snapshot_context
    public_headers = {
        "content-type": "application/json",
        "content-length": str(len(PAYLOAD)),
        "content-encoding": "identity",
        "etag": '"synthetic-etag"',
        "last-modified": "Sun, 18 Oct 2026 10:00:00 GMT",
        "cache-control": "max-age=60",
        "date": "Sun, 18 Oct 2026 11:00:00 GMT",
    }
    headers = {
        **public_headers,
        "set-cookie": "session=private-cookie",
        "authorization": "Bearer private-token",
        "location": "https://example.invalid/private-location",
        "x-api-key": "private-key",
    }

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(PAYLOAD))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        store = HttpSnapshotStore(
            context.external_dir, context.connection, context.run, client=client
        )
        before = context.run.clock.now()
        with context.run:
            with context.run.step("download") as step:
                snapshot = store.download("tse", URL)
        after = context.run.clock.now()

        assert snapshot.source == "tse"
        assert str(UUID(snapshot.snapshot_id)) == snapshot.snapshot_id
        assert snapshot.path.parent == (
            context.external_dir / "tse" / snapshot.snapshot_id
        )
        assert snapshot.path.read_bytes() == PAYLOAD
        manifest = snapshot.manifest
        assert manifest.source == snapshot.source
        assert manifest.snapshot_id == snapshot.snapshot_id
        assert manifest.url == URL
        assert manifest.status == 200
        assert manifest.headers == public_headers
        assert manifest.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        assert manifest.size_bytes == len(PAYLOAD)
        assert manifest.run_id == context.run.run_id
        assert manifest.step_id == step.record.step_id
        assert before <= manifest.fetched_at <= after
        assert manifest.fetched_at.utcoffset() is not None
        with pytest.raises(FrozenInstanceError):
            setattr(snapshot, "source", "altered")
        with pytest.raises(FrozenInstanceError):
            setattr(manifest, "url", "https://example.invalid/altered")

        saved = json.loads((snapshot.path.parent / "manifest.json").read_text())
        assert saved["headers"] == public_headers
        assert saved["sha256"] == manifest.sha256
        assert saved["run_id"] == context.run.run_id
        assert saved["step_id"] == step.record.step_id
        assert datetime.fromisoformat(saved["fetched_at"]) == manifest.fetched_at
        assert "private-" not in json.dumps(saved)

        # Historical replay is read-only and remains available after run completion.
        assert store.load("tse", snapshot.snapshot_id) == snapshot
        assert store.load_latest("tse") == snapshot
        assert store.list_snapshots("tse") == (snapshot,)
        assert not client.is_closed


def test_existing_snapshot_directory_is_never_overwritten(
    snapshot_context: SnapshotContext,
    recording_client: tuple[httpx.Client, list[httpx.Request]],
) -> None:
    context = snapshot_context
    client, _ = recording_client
    directory = context.external_dir / "tse" / SNAPSHOT_ID
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(b"original manifest")
    (directory / "payload.bin").write_bytes(b"original payload")
    original = {path.name: path.read_bytes() for path in directory.iterdir()}

    class CollisionIds:
        def __init__(self) -> None:
            self.used = False
            self.fallback = Uuid7Generator()

        def new_id(self) -> str:
            if not self.used:
                self.used = True
                return SNAPSHOT_ID
            return self.fallback.new_id()

    store = HttpSnapshotStore(
        context.external_dir, context.connection, context.run, client=client
    )
    with context.run:
        with context.run.step("collision"):
            context.run.ids = CollisionIds()
            with pytest.raises(FileExistsError):
                store.download("tse", URL)

    assert {path.name: path.read_bytes() for path in directory.iterdir()} == original
    assert context.connection.execute("SELECT count(*) FROM snapshots").fetchone() == (
        0,
    )


def test_source_directory_symlink_cannot_escape_the_snapshot_root(
    snapshot_context: SnapshotContext,
    recording_client: tuple[httpx.Client, list[httpx.Request]],
    tmp_path: Path,
) -> None:
    context = snapshot_context
    client, requests = recording_client
    outside = tmp_path / "unrelated"
    outside.mkdir()
    sentinel = outside / "original.txt"
    sentinel.write_bytes(b"preserve unrelated files")
    context.external_dir.mkdir()
    (context.external_dir / "tse").symlink_to(outside, target_is_directory=True)
    store = HttpSnapshotStore(
        context.external_dir, context.connection, context.run, client=client
    )

    with context.run:
        with context.run.step("reject_symlink"):
            with pytest.raises(ValueError):
                store.download("tse", URL)

    assert requests == []
    assert list(outside.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve unrelated files"
    assert context.connection.execute("SELECT count(*) FROM snapshots").fetchone() == (
        0,
    )

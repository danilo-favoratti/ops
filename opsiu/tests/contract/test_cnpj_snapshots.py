"""Local CNPJ snapshots preserve complete input bytes and immutable provenance."""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.external.cnpj_snapshots import LocalCnpjSnapshotStore
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import Uuid7Generator, collect_metadata
from opsiu.application.ports import CnpjSnapshotStore
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.contract
SNAPSHOT_ID = "01900000-0000-7000-8000-0000000000ab"


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class Context:
    connection: DuckDBPyConnection
    run: Run
    external: Path
    source: Path
    store: LocalCnpjSnapshotStore


@pytest.fixture
def context(tmp_path: Path) -> Iterator[Context]:
    with connect_database(tmp_path / "cnpj.duckdb") as connection:
        apply_migrations(connection)
        run = Run(
            ledger=CompositeLedger(
                JsonlLedger(tmp_path / "runs"), DuckDBLedger(connection)
            ),
            clock=FixedClock(),
            ids=Uuid7Generator(),
            command="CNPJ snapshot contract",
            arguments={},
            metadata=collect_metadata("settings-hash", tmp_path),
        )
        source = tmp_path / "source"
        source.mkdir()
        (source / "Empresas0.zip").write_bytes(b"synthetic company bytes")
        (source / "Socios0.csv").write_bytes(b"synthetic partner bytes")
        (source / "Estabelecimentos0.zip").write_bytes(b"synthetic office bytes")
        external = tmp_path / "external"
        yield Context(
            connection,
            run,
            external,
            source,
            LocalCnpjSnapshotStore(external, connection, run),
        )


def test_capture_requires_active_run_step_and_matching_database_ledger(
    context: Context,
) -> None:
    with pytest.raises(RuntimeError, match="not active"):
        context.store.capture(context.source)
    with context.run:
        with pytest.raises(RuntimeError, match="active run step"):
            context.store.capture(context.source)
        with context.run.step("capture") as step:
            context.connection.execute(
                "UPDATE steps SET status = 'failed' WHERE step_id = ?",
                [step.record.step_id],
            )
            with pytest.raises(RuntimeError, match="matching active"):
                context.store.capture(context.source)
    with pytest.raises(RuntimeError, match="not active"):
        context.store.capture(context.source)
    assert not context.external.exists()
    assert context.connection.execute(
        "SELECT count(*) FROM cnpj_snapshots"
    ).fetchone() == (0,)


def test_capture_replay_new_ids_stable_hash_and_original_paths(
    context: Context, tmp_path: Path
) -> None:
    (context.source / "ignore.txt").write_bytes(b"not a CNPJ family")
    (context.source / "unrecognized-directory").mkdir()
    duplicate = tmp_path / "duplicate-source"
    duplicate.mkdir()
    for path in context.source.iterdir():
        if path.is_file():
            (duplicate / path.name).write_bytes(path.read_bytes())
    store: CnpjSnapshotStore = context.store
    assert store.load_latest() is None
    assert store.list_snapshots() == ()
    with pytest.raises(LookupError):
        store.load(SNAPSHOT_ID)
    with context.run:
        with context.run.step("capture") as step:
            first = store.capture(context.source)
            second = store.capture(duplicate)
    assert first.snapshot_id != second.snapshot_id
    assert first.manifest.sha256 == second.manifest.sha256
    assert first.manifest.captured_at == second.manifest.captured_at
    assert first.manifest.source_directory == str(context.source)
    assert first.manifest.run_id == context.run.run_id
    assert first.manifest.step_id == step.record.step_id
    assert first.path == context.external / "cnpj" / first.snapshot_id
    assert len(first.manifest.files) == 3
    expected_hash_input = []
    for file in first.manifest.files:
        raw = (context.source / file.name).read_bytes()
        assert (first.path / "files" / file.name).read_bytes() == raw
        assert file.original_path == str(context.source / file.name)
        assert file.sha256 == hashlib.sha256(raw).hexdigest()
        assert file.size_bytes == len(raw)
        expected_hash_input.append(
            {
                "name": file.name,
                "family": file.family,
                "sha256": file.sha256,
                "size_bytes": file.size_bytes,
            }
        )
    canonical = json.dumps(
        expected_hash_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert first.manifest.sha256 == hashlib.sha256(canonical.encode()).hexdigest()
    (context.source / "Socios0.csv").unlink()
    assert store.load(first.snapshot_id) == first
    assert store.list_snapshots() == (second, first)
    assert store.load_latest() == second
    assert context.run.counters == {
        "cnpj_snapshots_captured": 2,
        "cnpj_snapshot_bytes": first.manifest.size_bytes * 2,
    }
    saved_run = json.loads(
        (tmp_path / "runs" / context.run.run_id / "manifest.json").read_text()
    )
    assert len(saved_run["cnpj_snapshots"]) == 2
    events = DuckDBLedger(context.connection).get_events(context.run.run_id)
    assert [event.kind for event in events].count("cnpj.snapshot_captured") == 2


def test_official_raw_suffixes_allow_successful_empty_snapshot(
    context: Context,
) -> None:
    for path in context.source.iterdir():
        path.unlink()
    for name in ("K3241.EMPRECSV", "K3242.SOCIOCSV", "K3243.ESTABELE"):
        (context.source / name).touch()
    with context.run:
        with context.run.step("empty_capture"):
            snapshot = context.store.capture(context.source)
    assert snapshot.manifest.size_bytes == 0
    assert {file.family for file in snapshot.manifest.files} == {
        "empresas",
        "socios",
        "estabelecimentos",
    }
    assert context.store.load(snapshot.snapshot_id) == snapshot


def test_relative_parent_source_and_external_paths_work(
    context: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working_directory = tmp_path / "working-directory"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    store = LocalCnpjSnapshotStore(Path("../external"), context.connection, context.run)
    with context.run:
        with context.run.step("relative_paths"):
            snapshot = store.capture(Path("../source"))
    assert snapshot.path == context.external / "cnpj" / snapshot.snapshot_id
    assert snapshot.manifest.source_directory == str(context.source)
    assert store.load(snapshot.snapshot_id) == snapshot


@pytest.mark.parametrize("family", ["Empresas", "Socios", "Estabelecimentos"])
def test_incomplete_source_does_not_publish_or_create_target(
    context: Context, family: str
) -> None:
    for path in context.source.glob(f"{family}*"):
        path.unlink()
    with context.run:
        with context.run.step("incomplete"):
            with pytest.raises(DomainError, match="requires empresas"):
                context.store.capture(context.source)
    assert not context.external.exists()


@pytest.mark.parametrize(
    "snapshot_id",
    ["", "../escape", SNAPSHOT_ID.upper(), SNAPSHOT_ID.replace("-", "")],
)
def test_noncanonical_identifiers_rejected(context: Context, snapshot_id: str) -> None:
    with pytest.raises(ValueError):
        context.store.load(snapshot_id)
    assert not context.external.exists()


@pytest.mark.parametrize("location", ["source", "input", "external", "family"])
def test_capture_rejects_symbolic_links(
    context: Context, tmp_path: Path, location: str
) -> None:
    source = context.source
    if location == "source":
        source = tmp_path / "linked-source"
        source.symlink_to(context.source, target_is_directory=True)
    elif location == "input":
        path = context.source / "Socios0.csv"
        path.unlink()
        path.symlink_to(context.source / "Empresas0.zip")
    elif location == "external":
        context.external.symlink_to(context.source, target_is_directory=True)
    else:
        context.external.mkdir()
        (context.external / "cnpj").symlink_to(context.source, target_is_directory=True)
    with context.run:
        with context.run.step("symlink"):
            with pytest.raises(DomainError, match="symbolic|regular"):
                context.store.capture(source)
    assert context.connection.execute(
        "SELECT count(*) FROM cnpj_snapshots"
    ).fetchone() == (0,)


@pytest.mark.parametrize(
    "corruption", ["payload", "manifest", "index", "missing", "extra", "symlink"]
)
def test_replay_rejects_corrupted_or_incomplete_snapshot(
    context: Context, corruption: str
) -> None:
    with context.run:
        with context.run.step("capture"):
            snapshot = context.store.capture(context.source)
    payload = snapshot.path / "files" / "Socios0.csv"
    if corruption == "payload":
        payload.write_bytes(b"changed contents")
    elif corruption == "manifest":
        (snapshot.path / "manifest.json").write_text("{}")
    elif corruption == "index":
        context.connection.execute(
            "UPDATE cnpj_snapshots SET size_bytes = size_bytes + 1"
        )
    elif corruption == "missing":
        payload.unlink()
    elif corruption == "extra":
        (snapshot.path / "files" / "extra.csv").touch()
    else:
        payload.unlink()
        payload.symlink_to(context.source / "Socios0.csv")
    with pytest.raises(DomainError):
        context.store.load(snapshot.snapshot_id)


def test_manifest_cannot_smuggle_paths_even_when_database_document_agrees(
    context: Context,
) -> None:
    with context.run:
        with context.run.step("capture"):
            snapshot = context.store.capture(context.source)
    path = snapshot.path / "manifest.json"
    document = json.loads(path.read_text())
    document["files"][0]["name"] = "../escaped.csv"
    encoded = json.dumps(document)
    path.write_text(encoded)
    context.connection.execute("UPDATE cnpj_snapshots SET manifest_json = ?", [encoded])
    with pytest.raises(DomainError, match="basenames"):
        context.store.load(snapshot.snapshot_id)


def test_existing_snapshot_collision_never_changes_original_files(
    context: Context,
) -> None:
    class CollisionIds:
        def new_id(self) -> str:
            return SNAPSHOT_ID

    directory = context.external / "cnpj" / SNAPSHOT_ID
    directory.mkdir(parents=True)
    original = directory / "keep.txt"
    original.write_bytes(b"original")
    with context.run:
        with context.run.step("collision"):
            previous_ids = context.run.ids
            context.run.ids = CollisionIds()
            try:
                with pytest.raises(FileExistsError):
                    context.store.capture(context.source)
            finally:
                context.run.ids = previous_ids
    assert original.read_bytes() == b"original"
    assert list(directory.iterdir()) == [original]
    assert context.store.list_snapshots() == ()


def test_failed_database_publication_cleans_only_unpublished_directory(
    context: Context,
) -> None:
    with context.run:
        with context.run.step("capture"):
            snapshot = context.store.capture(context.source)
            context.connection.execute(
                "CREATE UNIQUE INDEX test_unique_cnpj_hash ON cnpj_snapshots(sha256)"
            )
            with pytest.raises(duckdb.ConstraintException):
                context.store.capture(context.source)
    assert context.store.list_snapshots() == (snapshot,)
    assert list((context.external / "cnpj").iterdir()) == [snapshot.path]
    assert context.run.counters["cnpj_snapshots_captured"] == 1


def test_copy_reads_bounded_chunks_and_failure_leaves_no_published_files(
    context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = Path.open
    sizes: list[int] = []

    class FailingReader:
        def __init__(self, contents: BinaryIO) -> None:
            self.contents = contents

        def __enter__(self) -> "FailingReader":
            return self

        def __exit__(self, *arguments: object) -> None:
            self.contents.close()

        def read(self, size: int) -> bytes:
            sizes.append(size)
            if len(sizes) > 1:
                raise OSError("synthetic source read failure")
            return self.contents.read(size)

    def fail_source(path: Path, mode: str = "r", **kwargs: object) -> object:
        if path == context.source / "Socios0.csv" and mode == "rb":
            return FailingReader(cast(BinaryIO, original_open(path, mode)))
        # The monkeypatch accepts only the keyword options this adapter uses.
        encoding = kwargs.get("encoding")
        assert encoding is None or isinstance(encoding, str)
        return original_open(path, mode, encoding=encoding)

    with context.run:
        with context.run.step("copy_failure"):
            with monkeypatch.context() as patch:
                patch.setattr(Path, "open", fail_source)
                with pytest.raises(OSError, match="synthetic source"):
                    context.store.capture(context.source)
    assert sizes == [64 * 1024, 64 * 1024]
    assert context.store.list_snapshots() == ()
    assert list((context.external / "cnpj").iterdir()) == []
    assert context.run.counters == {}

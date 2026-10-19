"""External snapshots retain complete HTTP payloads and replay without requests."""

import hashlib
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from duckdb import DuckDBPyConnection
from typer.testing import CliRunner

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.external.snapshots import HttpSnapshotStore
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator
from opsiu.application.ports import RunMetadata
from opsiu.application.runs import Run
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration


@dataclass
class HttpFixture:
    url: str
    payload: bytes
    requests: list[str]


@pytest.fixture
def http_source() -> Iterator[HttpFixture]:
    payload = ("nota,valor\n" + "emissão,100.01\n" * 10_000).encode("utf-8")
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            if self.path == "/missing":
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/partial":
                self.send_response(200)
                self.send_header("Content-Length", "10000")
                self.end_headers()
                self.wfile.write(b"incomplete")
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("ETag", '"synthetic-v1"')
            self.send_header("Set-Cookie", "synthetic-private=do-not-retain")
            self.send_header("X-Private-Token", "synthetic-token")
            self.end_headers()
            for start in range(0, len(payload), 4096):
                self.wfile.write(payload[start : start + 4096])

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield HttpFixture(f"http://127.0.0.1:{server.server_port}", payload, requests)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndb_path="db.duckdb"\nruns_dir="runs"\nexternal_dir="external"\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["db", "migrate"])
    assert result.exit_code == 0, result.exception or result.output
    return tmp_path


def new_run(root: Path, connection: DuckDBPyConnection) -> Run:
    return Run(
        ledger=CompositeLedger(JsonlLedger(root / "runs"), DuckDBLedger(connection)),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="test snapshots",
        arguments={},
        metadata=RunMetadata("settings", "commit", False, "3.12", "test"),
    )


def test_repeated_downloads_keep_distinct_snapshots_and_replay_locally(
    workspace: Path,
    http_source: HttpFixture,
) -> None:
    with connect_database(workspace / "db.duckdb") as db:
        run = new_run(workspace, db)
        store = HttpSnapshotStore(workspace / "external", db, run)
        with run:
            with run.step("primeira_captura"):
                first = store.download("tse", http_source.url + "/payload")
            with run.step("segunda_captura"):
                second = store.download("tse", http_source.url + "/payload")
        assert run.status == "completed"
        assert first.snapshot_id != second.snapshot_id
        assert first.path != second.path
        assert (
            first.path.read_bytes() == second.path.read_bytes() == http_source.payload
        )
        assert first.manifest.sha256 == hashlib.sha256(http_source.payload).hexdigest()
        assert first.manifest.size_bytes == len(http_source.payload)
        assert first.manifest.run_id == run.run_id
        assert first.manifest.status == 200
        assert first.manifest.headers["etag"] == '"synthetic-v1"'
        assert "set-cookie" not in first.manifest.headers
        assert "x-private-token" not in first.manifest.headers
        assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (2,)
        request_count = len(http_source.requests)
        assert store.load("tse", first.snapshot_id) == first
        assert store.load_latest("tse") == second
        assert {snapshot.snapshot_id for snapshot in store.list_snapshots("tse")} == {
            first.snapshot_id,
            second.snapshot_id,
        }
        assert len(http_source.requests) == request_count


def test_error_status_and_interrupted_body_never_publish_snapshots(
    workspace: Path,
    http_source: HttpFixture,
) -> None:
    with connect_database(workspace / "db.duckdb") as db:
        run = new_run(workspace, db)
        store = HttpSnapshotStore(workspace / "external", db, run)
        with run:
            with run.step("http_404"):
                store.download("tse", http_source.url + "/missing")
            with run.step("corpo_interrompido"):
                store.download("tse", http_source.url + "/partial")
        assert run.status == "completed_with_errors"
        assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (0,)
        assert not store.list_snapshots("tse")
        assert not list((workspace / "external").rglob("manifest.json"))
        assert db.execute(
            "SELECT count(*) FROM steps WHERE run_id=? AND status='failed'",
            [run.run_id],
        ).fetchone() == (2,)


def test_changed_payload_is_rejected_during_offline_replay(
    workspace: Path,
    http_source: HttpFixture,
) -> None:
    with connect_database(workspace / "db.duckdb") as db:
        run = new_run(workspace, db)
        store = HttpSnapshotStore(workspace / "external", db, run)
        with run:
            with run.step("captura"):
                snapshot = store.download("cnpj", http_source.url + "/payload")
        assert run.status == "completed"
        snapshot.path.write_bytes(b"tampered")
        request_count = len(http_source.requests)
        with pytest.raises(ValueError):
            store.load_latest("cnpj")
        assert len(http_source.requests) == request_count

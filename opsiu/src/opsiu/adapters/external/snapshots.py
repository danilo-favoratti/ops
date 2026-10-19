"""Immutable HTTP snapshots indexed by DuckDB and verified before local replay.

Payloads are raw HTTP entity bytes: transfer framing is removed, but content
encoding (for example gzip) is retained. Readers use the saved content-encoding
header when decoding. The store requests identity encoding to avoid compression
when the server supports it. Injected clients remain owned by their caller.

The database is the publication index. Files are completed before the row is
inserted; an interrupted process may leave an unindexed directory, which readers
ignore. Ordinary failures remove only files created by that download. Published
files and rows are never updated or removed.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from pathlib import Path
from uuid import UUID

import httpx
from duckdb import DuckDBPyConnection
from pydantic import TypeAdapter

from opsiu.application.ports import (
    JsonValue,
    SnapshotManifest,
)
from opsiu.application.ports import (
    Snapshot as Snapshot,
)
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

_SOURCE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_HEADERS = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "etag",
        "last-modified",
        "cache-control",
        "date",
    }
)
_MANIFEST = TypeAdapter(SnapshotManifest)
_CHUNK_SIZE = 64 * 1024


def _source(source: str) -> str:
    if _SOURCE.fullmatch(source) is None:
        raise ValueError("source must be a lowercase ASCII path component")
    return source


def _identifier(snapshot_id: str) -> str:
    if str(UUID(snapshot_id)) != snapshot_id:
        raise ValueError("snapshot_id must be a canonical UUID")
    return snapshot_id


def _url(url: str) -> httpx.URL:
    parsed = httpx.URL(url)
    if parsed.scheme not in {"http", "https"} or not parsed.host:
        raise ValueError("snapshot URL must use HTTP or HTTPS with a host")
    if parsed.username or parsed.password:
        raise ValueError("snapshot URL must not contain credentials")
    return parsed


def _document(manifest: SnapshotManifest) -> dict[str, JsonValue]:
    return {
        "source": manifest.source,
        "snapshot_id": manifest.snapshot_id,
        "url": manifest.url,
        "status": manifest.status,
        "headers": {key: value for key, value in manifest.headers.items()},
        "sha256": manifest.sha256,
        "size_bytes": manifest.size_bytes,
        "fetched_at": manifest.fetched_at.isoformat(),
        "run_id": manifest.run_id,
        "step_id": manifest.step_id,
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_manifest(manifest: SnapshotManifest) -> None:
    _source(manifest.source)
    _identifier(manifest.snapshot_id)
    _identifier(manifest.run_id)
    _identifier(manifest.step_id)
    _url(manifest.url)
    if not 200 <= manifest.status <= 299:
        raise DomainError("snapshot response was not successful")
    if (
        manifest.size_bytes < 0
        or re.fullmatch(r"[0-9a-f]{64}", manifest.sha256) is None
    ):
        raise DomainError("snapshot hash or size is invalid")
    if manifest.fetched_at.utcoffset() is None:
        raise DomainError("snapshot fetched_at must include a timezone")
    if not set(manifest.headers) <= _HEADERS:
        raise DomainError("snapshot headers include fields outside the safe subset")


class HttpSnapshotStore:
    def __init__(
        self,
        external_dir: Path,
        connection: DuckDBPyConnection,
        run: Run,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.external_dir = external_dir.resolve()
        self.connection = connection
        self.run = run
        self.client = client

    def download(self, source: str, url: str) -> Snapshot:
        step_id = self.run.require_step()
        source = _source(source)
        requested_url = _url(url)
        self._require_database_step(step_id)
        snapshot_id = _identifier(self.run.ids.new_id())
        directory = self._directory(source, snapshot_id)
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()  # A reused ID must not acquire ownership of existing files.
        payload = directory / "payload"
        manifest_path = directory / "manifest.json"
        published = False
        try:
            digest = hashlib.sha256()
            size = 0
            client_context = (
                nullcontext(self.client)
                if self.client is not None
                else httpx.Client(timeout=30.0, follow_redirects=True)
            )
            with client_context as client:
                with client.stream(
                    "GET", requested_url, headers={"Accept-Encoding": "identity"}
                ) as response:
                    response.raise_for_status()
                    headers = {
                        key: value
                        for key, value in response.headers.items()
                        if key in _HEADERS
                    }
                    with payload.open("xb") as output:
                        for chunk in response.iter_raw(chunk_size=_CHUNK_SIZE):
                            self.run.require_step()
                            output.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    declared_length = headers.get("content-length")
                    if declared_length is not None and int(declared_length) != size:
                        raise DomainError("snapshot response content-length mismatch")
                    manifest = SnapshotManifest(
                        source=source,
                        snapshot_id=snapshot_id,
                        url=str(requested_url),
                        status=response.status_code,
                        headers=headers,
                        sha256=digest.hexdigest(),
                        size_bytes=size,
                        fetched_at=self.run.clock.now(),
                        run_id=self.run.run_id,
                        step_id=step_id,
                    )
                    _validate_manifest(manifest)
            document = _json(_document(manifest))
            with manifest_path.open("x", encoding="utf-8") as output:
                output.write(document + "\n")
            self._require_database_step(step_id)
            self.connection.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    snapshot_id,
                    source,
                    str(payload),
                    document,
                    manifest.url,
                    manifest.status,
                    _json(dict(manifest.headers)),
                    manifest.sha256,
                    manifest.size_bytes,
                    manifest.fetched_at,
                    manifest.run_id,
                    manifest.step_id,
                ],
            )
            published = True
        finally:
            if not published:
                # This directory was exclusively created by this call.
                manifest_path.unlink(missing_ok=True)
                payload.unlink(missing_ok=True)
                directory.rmdir()
        self._record(manifest, payload)
        return Snapshot(source, snapshot_id, payload, manifest)

    def list_snapshots(self, source: str) -> tuple[Snapshot, ...]:
        source = _source(source)
        rows = self.connection.execute(
            "SELECT CAST(snapshot_id AS VARCHAR) FROM snapshots WHERE source = ? "
            "ORDER BY fetched_at DESC, snapshot_id DESC",
            [source],
        ).fetchall()
        return tuple(self.load(source, str(row[0])) for row in rows)

    def load_latest(self, source: str) -> Snapshot | None:
        source = _source(source)
        row = self.connection.execute(
            "SELECT CAST(snapshot_id AS VARCHAR) FROM snapshots WHERE source = ? "
            "ORDER BY fetched_at DESC, snapshot_id DESC LIMIT 1",
            [source],
        ).fetchone()
        return None if row is None else self.load(source, str(row[0]))

    def load(self, source: str, snapshot_id: str) -> Snapshot:
        source = _source(source)
        snapshot_id = _identifier(snapshot_id)
        row = self.connection.execute(
            "SELECT path, manifest_json, url, status, headers_json, sha256, "
            "size_bytes, fetched_at, CAST(run_id AS VARCHAR), "
            "CAST(step_id AS VARCHAR) FROM snapshots "
            "WHERE source = ? AND snapshot_id = ?",
            [source, snapshot_id],
        ).fetchone()
        if row is None:
            raise LookupError(f"snapshot not found: {source}/{snapshot_id}")
        directory = self._directory(source, snapshot_id)
        payload = directory / "payload"
        if str(payload) != row[0]:
            raise DomainError("snapshot indexed path does not match its identity")
        if payload.is_symlink() or (directory / "manifest.json").is_symlink():
            raise DomainError("snapshot files must not be symbolic links")
        try:
            document = (directory / "manifest.json").read_text(encoding="utf-8")
            if json.loads(document) != json.loads(str(row[1])):
                raise DomainError("snapshot manifest does not match its database row")
            manifest = _MANIFEST.validate_json(document, strict=True)
            _validate_manifest(manifest)
            if manifest.source != source or manifest.snapshot_id != snapshot_id:
                raise DomainError("snapshot manifest identity mismatch")
            if (
                manifest.url,
                manifest.status,
                dict(manifest.headers),
                manifest.sha256,
                manifest.size_bytes,
                manifest.fetched_at,
                manifest.run_id,
                manifest.step_id,
            ) != (*row[2:4], json.loads(str(row[4])), *row[5:]):
                raise DomainError("snapshot manifest conflicts with indexed metadata")
            digest = hashlib.sha256()
            size = 0
            with payload.open("rb") as contents:
                while chunk := contents.read(_CHUNK_SIZE):
                    digest.update(chunk)
                    size += len(chunk)
            if size != manifest.size_bytes or digest.hexdigest() != manifest.sha256:
                raise DomainError("snapshot payload hash or size mismatch")
        except DomainError:
            raise
        except (OSError, ValueError) as error:
            raise DomainError(
                f"invalid local snapshot: {source}/{snapshot_id}"
            ) from error
        return Snapshot(source, snapshot_id, payload, manifest)

    def _directory(self, source: str, snapshot_id: str) -> Path:
        directory = self.external_dir / source / snapshot_id
        if directory.resolve() != directory:
            raise ValueError("snapshot directory must not traverse symbolic links")
        return directory

    def _require_database_step(self, step_id: str) -> None:
        if self.run.require_step() != step_id:
            raise RuntimeError("snapshot writes require the original active step")
        active = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [self.run.run_id, step_id],
        ).fetchone()
        if active is None:
            raise RuntimeError("snapshot writes require a matching active run and step")

    def _record(self, manifest: SnapshotManifest, path: Path) -> None:
        description = _document(manifest)
        description["path"] = str(path)
        snapshots = self.run.metadata.setdefault("snapshots", [])
        assert isinstance(snapshots, list)
        snapshots.append(description)
        self.run.counters["snapshots_downloaded"] = (
            self.run.counters.get("snapshots_downloaded", 0) + 1
        )
        self.run.counters["snapshot_bytes"] = (
            self.run.counters.get("snapshot_bytes", 0) + manifest.size_bytes
        )
        self.run.emit("snapshot.downloaded", description)

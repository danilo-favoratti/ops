"""Immutable local Receita Federal snapshots with verified offline replay.

Only the DuckDB index publishes a snapshot. Failed captures remove their own
unpublished directory; existing snapshots and their source files are untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from uuid import UUID

from duckdb import DuckDBPyConnection
from pydantic import TypeAdapter

from opsiu.application.ports import (
    CnpjFamily,
    CnpjSnapshot,
    CnpjSnapshotFile,
    CnpjSnapshotManifest,
    JsonValue,
)
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

_CHUNK_SIZE = 64 * 1024
_MANIFEST = TypeAdapter(CnpjSnapshotManifest)
_FAMILIES: tuple[CnpjFamily, ...] = ("empresas", "socios", "estabelecimentos")
_SUFFIXES: dict[str, CnpjFamily] = {
    ".EMPRECSV": "empresas",
    ".SOCIOCSV": "socios",
    ".ESTABELE": "estabelecimentos",
}


def _identifier(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("snapshot identifiers must be canonical UUIDs")
    return value


def _basename(name: str) -> str:
    if (
        not name
        or name in {".", ".."}
        or Path(name).name != name
        or "\\" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise DomainError("snapshot file names must be plain basenames")
    return name


def _family(name: str) -> CnpjFamily | None:
    suffix = Path(name).suffix.upper()
    if suffix in _SUFFIXES:
        return _SUFFIXES[suffix]
    if suffix in {".ZIP", ".CSV"}:
        for family in _FAMILIES:
            if name.lower().startswith(family):
                return family
    return None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _file_document(file: CnpjSnapshotFile) -> dict[str, JsonValue]:
    return {
        "name": file.name,
        "family": file.family,
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
        "original_path": file.original_path,
    }


def _aggregate(files: tuple[CnpjSnapshotFile, ...]) -> str:
    contents = [
        {
            "name": file.name,
            "family": file.family,
            "sha256": file.sha256,
            "size_bytes": file.size_bytes,
        }
        for file in sorted(files, key=lambda file: file.name)
    ]
    return hashlib.sha256(_json(contents).encode("utf-8")).hexdigest()


def _document(manifest: CnpjSnapshotManifest) -> dict[str, JsonValue]:
    return {
        "snapshot_id": manifest.snapshot_id,
        "source_directory": manifest.source_directory,
        "sha256": manifest.sha256,
        "size_bytes": manifest.size_bytes,
        "captured_at": manifest.captured_at.isoformat(),
        "run_id": manifest.run_id,
        "step_id": manifest.step_id,
        "files": [_file_document(file) for file in manifest.files],
    }


def _validate_manifest(manifest: CnpjSnapshotManifest) -> None:
    _identifier(manifest.snapshot_id)
    _identifier(manifest.run_id)
    _identifier(manifest.step_id)
    if manifest.captured_at.utcoffset() is None:
        raise DomainError("snapshot captured_at must include a timezone")
    if not Path(manifest.source_directory).is_absolute():
        raise DomainError("snapshot source directory must be absolute")
    names: list[str] = []
    for file in manifest.files:
        names.append(_basename(file.name))
        if _family(file.name) != file.family:
            raise DomainError("snapshot file family does not match its name")
        if file.size_bytes < 0 or re.fullmatch(r"[0-9a-f]{64}", file.sha256) is None:
            raise DomainError("snapshot file hash or size is invalid")
        if file.original_path != str(Path(manifest.source_directory) / file.name):
            raise DomainError("snapshot original file path conflicts with its source")
    if names != sorted(set(names)):
        raise DomainError("snapshot file names must be unique and sorted")
    if {file.family for file in manifest.files} != set(_FAMILIES):
        raise DomainError("snapshot requires empresas, socios and estabelecimentos")
    if manifest.size_bytes != sum(file.size_bytes for file in manifest.files):
        raise DomainError("snapshot aggregate size is invalid")
    if manifest.sha256 != _aggregate(manifest.files):
        raise DomainError("snapshot aggregate hash is invalid")


class LocalCnpjSnapshotStore:
    def __init__(
        self,
        external_dir: Path,
        connection: DuckDBPyConnection,
        run: Run,
    ) -> None:
        self.external_dir = Path(os.path.abspath(external_dir))
        self.connection = connection
        self.run = run

    def capture(self, directory: Path) -> CnpjSnapshot:
        step_id = self.run.require_step()
        self._require_database_step(step_id)
        source = Path(os.path.abspath(directory))
        if source.resolve() != source or not source.is_dir():
            raise DomainError("CNPJ source must be a directory without symbolic links")
        inputs: list[tuple[Path, CnpjFamily]] = []
        for path in sorted(source.iterdir(), key=lambda path: path.name):
            family = _family(path.name)
            if family is None:
                continue
            _basename(path.name)
            if path.is_symlink() or not path.is_file():
                raise DomainError("CNPJ source files must be regular files")
            inputs.append((path, family))
        if {family for _, family in inputs} != set(_FAMILIES):
            raise DomainError("snapshot requires empresas, socios and estabelecimentos")
        snapshot_id = _identifier(self.run.ids.new_id())
        target = self._directory(snapshot_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()
        published = False
        try:
            files_dir = target / "files"
            files_dir.mkdir()
            files: list[CnpjSnapshotFile] = []
            for original, family in inputs:
                digest = hashlib.sha256()
                size = 0
                if original.is_symlink():
                    raise DomainError("CNPJ source files must not be symbolic links")
                with original.open("rb") as contents:
                    with (files_dir / original.name).open("xb") as output:
                        while chunk := contents.read(_CHUNK_SIZE):
                            self.run.require_step()
                            output.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                files.append(
                    CnpjSnapshotFile(
                        original.name, family, digest.hexdigest(), size, str(original)
                    )
                )
            manifest = CnpjSnapshotManifest(
                snapshot_id=snapshot_id,
                source_directory=str(source),
                sha256=_aggregate(tuple(files)),
                size_bytes=sum(file.size_bytes for file in files),
                captured_at=self.run.clock.now(),
                run_id=self.run.run_id,
                step_id=step_id,
                files=tuple(files),
            )
            _validate_manifest(manifest)
            document = _json(_document(manifest))
            with (target / "manifest.json").open("x", encoding="utf-8") as output:
                output.write(document + "\n")
            self._require_database_step(step_id)
            self.connection.execute(
                "INSERT INTO cnpj_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    snapshot_id,
                    str(target),
                    str(source),
                    document,
                    manifest.sha256,
                    manifest.size_bytes,
                    manifest.captured_at,
                    manifest.run_id,
                    manifest.step_id,
                ],
            )
            published = True
        finally:
            if not published:
                shutil.rmtree(target)
        self._record(manifest, target)
        return CnpjSnapshot(snapshot_id, target, manifest)

    def list_snapshots(self) -> tuple[CnpjSnapshot, ...]:
        rows = self.connection.execute(
            "SELECT CAST(snapshot_id AS VARCHAR) FROM cnpj_snapshots "
            "ORDER BY captured_at DESC, snapshot_id DESC"
        ).fetchall()
        return tuple(self.load(str(row[0])) for row in rows)

    def load_latest(self) -> CnpjSnapshot | None:
        row = self.connection.execute(
            "SELECT CAST(snapshot_id AS VARCHAR) FROM cnpj_snapshots "
            "ORDER BY captured_at DESC, snapshot_id DESC LIMIT 1"
        ).fetchone()
        return None if row is None else self.load(str(row[0]))

    def load(self, snapshot_id: str) -> CnpjSnapshot:
        snapshot_id = _identifier(snapshot_id)
        row = self.connection.execute(
            "SELECT path, source_directory, manifest_json, sha256, size_bytes, "
            "captured_at, CAST(run_id AS VARCHAR), CAST(step_id AS VARCHAR) "
            "FROM cnpj_snapshots WHERE snapshot_id = ?",
            [snapshot_id],
        ).fetchone()
        if row is None:
            raise LookupError(f"CNPJ snapshot not found: {snapshot_id}")
        target = self._directory(snapshot_id)
        if str(target) != row[0]:
            raise DomainError("snapshot indexed path does not match its identity")
        manifest_path = target / "manifest.json"
        files_dir = target / "files"
        if manifest_path.is_symlink() or files_dir.is_symlink():
            raise DomainError("snapshot files must not be symbolic links")
        try:
            document = manifest_path.read_text(encoding="utf-8")
            if json.loads(document) != json.loads(str(row[2])):
                raise DomainError("snapshot manifest does not match its database row")
            manifest = _MANIFEST.validate_json(document, strict=True)
            _validate_manifest(manifest)
            if json.loads(document) != _document(manifest):
                raise DomainError("snapshot manifest has unexpected fields or values")
            if manifest.snapshot_id != snapshot_id:
                raise DomainError("snapshot manifest identity mismatch")
            if (
                manifest.source_directory,
                manifest.sha256,
                manifest.size_bytes,
                manifest.captured_at,
                manifest.run_id,
                manifest.step_id,
            ) != (row[1], *row[3:]):
                raise DomainError("snapshot manifest conflicts with indexed metadata")
            if {path.name for path in files_dir.iterdir()} != {
                file.name for file in manifest.files
            }:
                raise DomainError("snapshot file inventory conflicts with its manifest")
            for file in manifest.files:
                path = files_dir / file.name
                if path.is_symlink() or not path.is_file():
                    raise DomainError("snapshot files must be regular files")
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as contents:
                    while chunk := contents.read(_CHUNK_SIZE):
                        digest.update(chunk)
                        size += len(chunk)
                if size != file.size_bytes or digest.hexdigest() != file.sha256:
                    raise DomainError("snapshot file hash or size mismatch")
        except DomainError:
            raise
        except (OSError, ValueError) as error:
            raise DomainError(f"invalid local CNPJ snapshot: {snapshot_id}") from error
        return CnpjSnapshot(snapshot_id, target, manifest)

    def _directory(self, snapshot_id: str) -> Path:
        directory = self.external_dir / "cnpj" / snapshot_id
        if directory.resolve() != directory:
            raise DomainError("snapshot directory must not traverse symbolic links")
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

    def _record(self, manifest: CnpjSnapshotManifest, path: Path) -> None:
        description = _document(manifest)
        description["path"] = str(path)
        snapshots = self.run.metadata.setdefault("cnpj_snapshots", [])
        assert isinstance(snapshots, list)
        snapshots.append(description)
        self.run.counters["cnpj_snapshots_captured"] = (
            self.run.counters.get("cnpj_snapshots_captured", 0) + 1
        )
        self.run.counters["cnpj_snapshot_bytes"] = (
            self.run.counters.get("cnpj_snapshot_bytes", 0) + manifest.size_bytes
        )
        self.run.emit("cnpj.snapshot_captured", description)

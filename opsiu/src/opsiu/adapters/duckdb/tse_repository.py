"""Atomic append-only TSE candidate publication with successful-import views."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from duckdb import DuckDBPyConnection

from opsiu.application.ports import Snapshot, TseCandidate, TseImport, TseStage
from opsiu.domain.errors import DomainError
from opsiu.domain.policies import ELECTION_DATES


class DuckDBTseRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def current_import(self, year: int) -> TseImport | None:
        row = self.connection.execute(
            "SELECT import_id, snapshot_id, source_sha256, year, records_count "
            "FROM tse_imports_current WHERE year = ?",
            [year],
        ).fetchone()
        return (
            None
            if row is None
            else TseImport(str(row[0]), str(row[1]), str(row[2]), row[3], row[4])
        )

    @contextmanager
    def stage(
        self,
        *,
        import_id: str,
        snapshot: Snapshot,
        year: int,
        run_id: str,
        step_id: str,
    ) -> Iterator[TseStage]:
        stage = _TseStage(self.connection, import_id, snapshot, year, run_id, step_id)
        stage.require_active()
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute(
                "CREATE TEMP TABLE tse_stage AS SELECT * FROM tse_candidatos LIMIT 0"
            )
            yield stage
        finally:
            if not stage.published:
                self.connection.execute("ROLLBACK")


class _TseStage:
    def __init__(
        self,
        connection: DuckDBPyConnection,
        import_id: str,
        snapshot: Snapshot,
        year: int,
        run_id: str,
        step_id: str,
    ) -> None:
        self.connection = connection
        self.import_id = str(UUID(import_id))
        self.snapshot = snapshot
        self.year = year
        self.run_id = run_id
        self.step_id = step_id
        self.count = 0
        self.published = False
        if year not in ELECTION_DATES or snapshot.source != f"tse_{year}":
            raise DomainError("TSE import requires a snapshot for its election year")

    def require_active(self) -> None:
        if self.published:
            raise RuntimeError("the TSE import has already been published")
        row = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [self.run_id, self.step_id],
        ).fetchone()
        if row is None:
            raise RuntimeError("TSE writes require a matching active run and step")

    def add_batch(self, candidates: tuple[TseCandidate, ...]) -> None:
        self.require_active()
        if any(candidate.year != self.year for candidate in candidates):
            raise DomainError("TSE batch includes a different election year")
        if not candidates:
            return
        schema = {
            "candidate_id": "VARCHAR",
            "year": "INTEGER",
            "cpf": "VARCHAR",
            "nome_normalizado": "VARCHAR",
            "uf": "VARCHAR",
            "cargo": "VARCHAR",
            "source_member": "VARCHAR",
            "source_record_number": "BIGINT",
        }
        self.connection.execute(
            "INSERT INTO tse_stage SELECT ?, ?, candidate.year, "
            "candidate.candidate_id, candidate.cpf, candidate.nome_normalizado, "
            "candidate.uf, candidate.cargo, candidate.source_member, "
            "candidate.source_record_number, ?, ? "
            "FROM (SELECT unnest(from_json_strict(?, ?)) AS candidate)",
            [
                self.import_id,
                self.snapshot.snapshot_id,
                self.run_id,
                self.step_id,
                json.dumps([asdict(candidate) for candidate in candidates]),
                json.dumps([schema]),
            ],
        )
        self.count += len(candidates)

    def publish(self, *, loaded_at: datetime) -> TseImport:
        self.require_active()
        origin = self.connection.execute(
            "SELECT source, sha256 FROM snapshots WHERE snapshot_id = ?",
            [self.snapshot.snapshot_id],
        ).fetchone()
        if origin != (self.snapshot.source, self.snapshot.manifest.sha256):
            raise DomainError("TSE snapshot provenance does not match its index")
        conflicting = self.connection.execute(
            "SELECT candidate_id FROM tse_stage GROUP BY candidate_id HAVING "
            "count(DISTINCT (cpf, nome_normalizado, uf, cargo)) > 1 LIMIT 1"
        ).fetchone()
        if conflicting is not None:
            raise DomainError("TSE candidate ID has conflicting identity metadata")
        count = self.connection.execute(
            "SELECT count(DISTINCT candidate_id) FROM tse_stage"
        ).fetchone()
        assert count is not None
        self.count = int(count[0])
        self.connection.execute(
            "INSERT INTO tse_imports VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                self.import_id,
                self.snapshot.snapshot_id,
                self.year,
                self.count,
                loaded_at,
                self.run_id,
                self.step_id,
            ],
        )
        self.connection.execute(
            "INSERT INTO tse_candidatos SELECT * FROM tse_stage "
            "QUALIFY row_number() OVER (PARTITION BY candidate_id "
            "ORDER BY source_member, source_record_number) = 1"
        )
        self.connection.execute("DROP TABLE tse_stage")
        self.connection.execute("COMMIT")
        self.published = True
        return TseImport(
            self.import_id,
            self.snapshot.snapshot_id,
            self.snapshot.manifest.sha256,
            self.year,
            self.count,
        )

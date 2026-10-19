"""Filtered Receita history with atomic publication and explicit root coverage."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from duckdb import DuckDBPyConnection

from opsiu.application.ports import (
    CnpjCompany,
    CnpjEstablishment,
    CnpjImport,
    CnpjRecord,
    CnpjSnapshot,
    CnpjStage,
)
from opsiu.domain.errors import DomainError

_TABLES = ("cnpj_empresas", "cnpj_socios", "cnpj_estabelecimentos")


class DuckDBCnpjRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def requested_roots(self) -> frozenset[str]:
        return frozenset(
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT substr(cnpj_cpf_digits, 1, 8) FROM despesas_current "
                "WHERE regexp_full_match(cnpj_cpf_digits, '[0-9]{14}')"
            ).fetchall()
        )

    def current_import(self) -> CnpjImport | None:
        row = self.connection.execute(
            "SELECT import_id, snapshot_id, source_sha256, roots_sha256, "
            "companies, partners, establishments FROM cnpj_imports_current"
        ).fetchone()
        return (
            None
            if row is None
            else CnpjImport(
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                row[4],
                row[5],
                row[6],
            )
        )

    def canonical_snapshot_id(self, sha256: str) -> str | None:
        row = self.connection.execute(
            "SELECT i.snapshot_id FROM cnpj_imports i "
            "JOIN cnpj_snapshots s USING (snapshot_id) WHERE s.sha256 = ? "
            "ORDER BY i.loaded_at, i.import_id LIMIT 1",
            [sha256],
        ).fetchone()
        return None if row is None else str(row[0])

    @contextmanager
    def stage(
        self,
        *,
        import_id: str,
        snapshot: CnpjSnapshot,
        roots: frozenset[str],
        roots_sha256: str,
        run_id: str,
        step_id: str,
    ) -> Iterator[CnpjStage]:
        stage = _CnpjStage(
            self.connection, import_id, snapshot, roots, roots_sha256, run_id, step_id
        )
        stage.require_active()
        created: list[str] = []
        try:
            # Temporary staging is outside the final transaction: progress events
            # survive a later parser failure in both the file and database ledgers.
            for table in _TABLES:
                self.connection.execute(
                    f"CREATE TEMP TABLE {table}_stage AS SELECT * FROM {table} LIMIT 0"
                )
                created.append(table)
            yield stage
        finally:
            stage.closed = True
            for table in created:
                self.connection.execute(f"DROP TABLE {table}_stage")


class _CnpjStage:
    def __init__(
        self,
        connection: DuckDBPyConnection,
        import_id: str,
        snapshot: CnpjSnapshot,
        roots: frozenset[str],
        roots_sha256: str,
        run_id: str,
        step_id: str,
    ) -> None:
        self.connection = connection
        self.import_id = str(UUID(import_id))
        self.snapshot = snapshot
        self.roots = roots
        self.roots_sha256 = roots_sha256
        self.run_id = run_id
        self.step_id = step_id
        self.closed = False
        self.published = False

    def require_active(self) -> None:
        if self.closed or self.published:
            raise RuntimeError("the CNPJ stage is no longer writable")
        row = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [self.run_id, self.step_id],
        ).fetchone()
        if row is None:
            raise RuntimeError("CNPJ writes require a matching active run and step")

    def add_batch(self, records: tuple[CnpjRecord, ...]) -> None:
        self.require_active()
        grouped: dict[str, list[dict[str, object]]] = {table: [] for table in _TABLES}
        files = {file.name: file for file in self.snapshot.manifest.files}
        for record in records:
            if record.cnpj_root not in self.roots:
                raise DomainError("CNPJ record is outside the requested root coverage")
            file = files.get(record.origin.source_file)
            if file is None or file.sha256 != record.origin.source_sha256:
                raise DomainError("CNPJ record file origin does not match its snapshot")
            table = (
                "cnpj_empresas"
                if isinstance(record, CnpjCompany)
                else "cnpj_estabelecimentos"
                if isinstance(record, CnpjEstablishment)
                else "cnpj_socios"
            )
            row: dict[str, object] = asdict(record)
            del row["origin"]
            row.update(asdict(record.origin))
            row.update(
                import_id=self.import_id,
                snapshot_id=self.snapshot.snapshot_id,
                run_id=self.run_id,
                step_id=self.step_id,
            )
            grouped[table].append(row)
        for table, rows in grouped.items():
            if not rows:
                continue
            schema = {
                str(row[0]): str(row[1])
                for row in self.connection.execute(f"DESCRIBE {table}_stage").fetchall()
            }
            self.connection.execute(
                f"INSERT INTO {table}_stage SELECT record.* FROM "
                "(SELECT unnest(from_json_strict(?, ?)) AS record)",
                [json.dumps(rows, ensure_ascii=False), json.dumps([schema])],
            )

    def publish(self, *, loaded_at: datetime) -> CnpjImport:
        self.require_active()
        origin = self.connection.execute(
            "SELECT sha256 FROM cnpj_snapshots WHERE snapshot_id = ?",
            [self.snapshot.snapshot_id],
        ).fetchone()
        if origin != (self.snapshot.manifest.sha256,):
            raise DomainError("CNPJ snapshot origin does not match its database row")
        selects: dict[str, str] = {}
        counts: dict[str, int] = {}
        for table in _TABLES:
            query = f"SELECT * FROM {table}_stage"
            if table != "cnpj_socios":
                key, values = (
                    ("cnpj_root", "razao_social, natureza_juridica")
                    if table == "cnpj_empresas"
                    else ("cnpj", "cnpj_root, cnae_principal")
                )
                conflict = self.connection.execute(
                    f"SELECT {key} FROM {table}_stage GROUP BY {key} "
                    f"HAVING count(DISTINCT ({values})) > 1 LIMIT 1"
                ).fetchone()
                if conflict is not None:
                    raise DomainError(f"conflicting Receita identity in {table}")
                query += (
                    f" QUALIFY row_number() OVER (PARTITION BY {key} "
                    "ORDER BY source_file, source_member, source_record_number) = 1"
                )
            selects[table] = query
            count = self.connection.execute(
                f"SELECT count(*) FROM ({query})"
            ).fetchone()
            assert count is not None
            counts[table] = int(count[0])
        result = CnpjImport(
            self.import_id,
            self.snapshot.snapshot_id,
            self.snapshot.manifest.sha256,
            self.roots_sha256,
            *(counts[table] for table in _TABLES),
        )
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute(
                "INSERT INTO cnpj_imports VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    self.import_id,
                    self.snapshot.snapshot_id,
                    self.roots_sha256,
                    result.companies,
                    result.partners,
                    result.establishments,
                    loaded_at,
                    self.run_id,
                    self.step_id,
                ],
            )
            self.connection.execute(
                "INSERT INTO cnpj_import_roots "
                "SELECT ?, unnest(from_json_strict(?, ?))",
                [self.import_id, json.dumps(sorted(self.roots)), '["VARCHAR"]'],
            )
            for table, query in selects.items():
                self.connection.execute(f"INSERT INTO {table} {query}")
            self.connection.execute("COMMIT")
            self.published = True
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return result

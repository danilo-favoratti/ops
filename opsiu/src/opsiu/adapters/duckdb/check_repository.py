"""Read-only rule candidates and transactional finding publication."""

import json
from collections.abc import Iterator, Mapping
from datetime import datetime

from duckdb import DuckDBPyConnection, StatementType

from opsiu.application.ports import ExpenseSourceMetadata, FindingChanges
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import Finding, SourceOrigin, evidence_json


class DuckDBCheckRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def has_relation(self, name: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = ?",
                [name],
            ).fetchone()
            is not None
        )

    def candidates(self, sql: str) -> Iterator[Mapping[str, object]]:
        statements = self.connection.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != StatementType.SELECT:
            raise DomainError("rule SQL must contain exactly one SELECT statement")
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)
            names = [column[0] for column in cursor.description]
            if len(names) != len(set(names)):
                raise DomainError("rule SQL candidate columns must have unique names")
            while batch := cursor.fetchmany(1_000):
                for row in batch:
                    yield dict(zip(names, row, strict=True))
        finally:
            cursor.close()

    def source_origins(self, id_documento: int) -> tuple[SourceOrigin, ...]:
        rows = self.connection.execute(
            "SELECT DISTINCT d.source_file_id, f.path, f.sha256, d.record_sha256 "
            "FROM despesas_current d JOIN source_files f USING (source_file_id) "
            "WHERE d.id_documento = ? ORDER BY f.path, d.record_sha256",
            [id_documento],
        ).fetchall()
        return tuple(SourceOrigin(*(str(value) for value in row)) for row in rows)

    def source_origin(self, id_documento: int) -> SourceOrigin:
        rows = self.source_origins(id_documento)
        if len(rows) != 1:
            raise DomainError(
                f"finding document {id_documento} has missing or ambiguous "
                "current origin"
            )
        return rows[0]

    def current_sources(self) -> tuple[ExpenseSourceMetadata, ...]:
        rows = self.connection.execute(
            "SELECT path, sha256, size FROM source_files WHERE source_year IS NOT NULL "
            "QUALIFY row_number() OVER (PARTITION BY source_year "
            "ORDER BY ingested_at DESC, source_file_id DESC) = 1 ORDER BY source_year"
        ).fetchall()
        return tuple(
            ExpenseSourceMetadata(str(path), str(sha), int(size))
            for path, sha, size in rows
        )

    def publish_findings(
        self,
        rule_id: str,
        rule_version: str,
        findings: tuple[Finding, ...],
        *,
        run_id: str,
        step_id: str,
        finished_at: datetime,
    ) -> FindingChanges:
        active = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [run_id, step_id],
        ).fetchone()
        if active is None:
            raise RuntimeError("finding writes require a matching active run and step")
        keys = {finding.finding_key for finding in findings}
        if len(keys) != len(findings):
            raise DomainError("a rule emitted duplicate finding identities")
        if any(
            finding.rule_id != rule_id
            or finding.rule_version != rule_version
            or finding.provenance.run_id != run_id
            or finding.provenance.step_id != step_id
            or finding.status != "active"
            or finding.superseded_at is not None
            or finding.superseded_by_run_id is not None
            for finding in findings
        ):
            raise DomainError(
                "finding publication rule/run/step does not match its origin"
            )
        self.connection.execute("BEGIN TRANSACTION")
        inserted = unchanged = superseded = 0
        try:
            for finding in findings:
                source = finding.provenance.source
                if source not in self.source_origins(finding.id_documento):
                    raise DomainError("finding origin is not the current source record")
                evidence = evidence_json(finding.evidence)
                existing = self.connection.execute(
                    "SELECT severity, amount, evidence_json, record_sha256, status "
                    "FROM findings WHERE finding_key = ?",
                    [finding.finding_key],
                ).fetchone()
                if existing is not None:
                    expected = (
                        finding.severity,
                        finding.amount,
                        evidence,
                        source.record_sha256,
                        "active",
                    )
                    actual = (
                        existing[0],
                        existing[1],
                        evidence_json(json.loads(existing[2])),
                        existing[3],
                        existing[4],
                    )
                    if actual != expected:
                        raise DomainError(
                            f"immutable finding conflict: {finding.finding_key}; "
                            "existing evidence or superseded history "
                            "cannot be overwritten"
                        )
                    unchanged += 1
                    continue
                self.connection.execute(
                    "INSERT INTO finding_records (finding_key, rule_id, rule_version, "
                    "id_documento, severity, amount, evidence_json, "
                    "run_id, step_id, source_file_id, "
                    "record_sha256, code_commit, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        finding.finding_key,
                        rule_id,
                        rule_version,
                        finding.id_documento,
                        finding.severity,
                        finding.amount,
                        evidence,
                        run_id,
                        step_id,
                        source.source_file_id,
                        source.record_sha256,
                        finding.provenance.code_commit,
                        finding.provenance.created_at,
                    ],
                )
                inserted += 1
            # Only a complete successful evaluation may supersede earlier results.
            active_keys = self.connection.execute(
                "SELECT finding_key FROM findings "
                "WHERE rule_id = ? AND status = 'active'",
                [rule_id],
            ).fetchall()
            for (key,) in active_keys:
                if str(key) not in keys:
                    self.connection.execute(
                        "INSERT INTO finding_supersessions "
                        "(finding_key, superseded_at, superseded_by_run_id) "
                        "VALUES (?, ?, ?)",
                        [key, finished_at, run_id],
                    )
                    superseded += 1
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return FindingChanges(inserted, unchanged, superseded)

"""Read original finding evidence and append human review decisions."""

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from duckdb import DuckDBPyConnection
from pydantic import TypeAdapter

from opsiu.adapters.duckdb.pdf_repository import DuckDBPdfRepository
from opsiu.application.ports import (
    DeputyOption,
    FindingExportRecord,
    JsonValue,
    ReviewAnnotation,
    ReviewDetail,
    ReviewFilters,
    ReviewItem,
)
from opsiu.domain.decisions import Decision
from opsiu.domain.errors import DomainError
from opsiu.domain.models import Despesa

_RECORD = TypeAdapter(FindingExportRecord)
_EXPENSE = TypeAdapter(Despesa)
_DECISION = TypeAdapter(Decision)
_ANNOTATION = TypeAdapter(ReviewAnnotation)

_ORIGINAL_FINDINGS = """
    SELECT f.*, s.path AS source_file, s.sha256 AS source_sha256,
           s.size AS source_size_bytes,
           d.* EXCLUDE (id_documento, source_file_id, record_sha256, run_id, step_id)
    FROM findings_current f JOIN source_files s USING (source_file_id)
    LEFT JOIN LATERAL (
        SELECT * FROM despesas
        WHERE source_file_id = f.source_file_id
          AND id_documento = f.id_documento AND record_sha256 = f.record_sha256
        ORDER BY source_record_number LIMIT 1
    ) d ON TRUE
"""


_PENDING_WHERE = """
                WHERE f.status = 'active'
                  AND (f.decision IS NULL OR f.decision = 'revisar')
                  AND (? IS NULL OR f.rule_id = ?)
                  AND (? IS NULL OR f.amount >= ?)
                  AND (d.source_record_number IS NULL OR (
                      (? OR d.numero_deputado_id IN (SELECT unnest(?)))
                      AND (? OR d.sigla_uf IN (SELECT unnest(?)))
                  ))
"""


def _pending_parameters(filters: ReviewFilters) -> list[object]:
    deputies = list(filters.deputy_ids) + (
        [filters.deputy_id] if filters.deputy_id is not None else []
    )
    ufs = list(filters.ufs) + ([filters.uf] if filters.uf is not None else [])
    return [
        filters.rule_id,
        filters.rule_id,
        filters.min_amount,
        filters.min_amount,
        not deputies,
        deputies,
        not ufs,
        ufs,
    ]


def _object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, str):
        raise DomainError("review evidence requires stored JSON")
    try:
        decoded: object = json.loads(value)
        if not isinstance(decoded, dict):
            raise DomainError("review evidence requires a JSON object")
        json.dumps(decoded, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise DomainError("review evidence contains invalid JSON") from error
    return cast(dict[str, JsonValue], decoded)


def _record(row: Mapping[str, object]) -> FindingExportRecord:
    if row["source_record_number"] is None:
        raise DomainError("finding has no matching original expense record")
    finding: dict[str, object] = {
        key: row[key]
        for key in (
            "rule_id",
            "rule_version",
            "id_documento",
            "severity",
            "amount",
            "status",
            "superseded_at",
        )
    }
    finding["superseded_by_run_id"] = (
        str(row["superseded_by_run_id"])
        if row["superseded_by_run_id"] is not None
        else None
    )
    finding["record_identity"] = row.get("record_identity", "")
    finding["evidence"] = _object(row["evidence_json"])
    finding["provenance"] = {
        "run_id": str(row["run_id"]),
        "step_id": str(row["step_id"]),
        "code_commit": row["code_commit"],
        "created_at": row["created_at"],
        "source": {
            key: str(row[key])
            for key in (
                "source_file_id",
                "source_file",
                "source_sha256",
                "record_sha256",
            )
        },
    }
    result = _RECORD.validate_python(
        {
            "finding": finding,
            **{
                key: row[key]
                for key in FindingExportRecord.__dataclass_fields__
                if key != "finding"
            },
        }
    )
    if result.finding.finding_key != row["finding_key"]:
        raise DomainError("stored finding identity does not match its key")
    return result


def _expense(row: Mapping[str, object]) -> Despesa:
    return _EXPENSE.validate_python(
        {
            **{
                key: row[key]
                for key in Despesa.__dataclass_fields__
                if key not in ("competencia", "cnpj_cpf")
            },
            "competencia": {"ano": row["ano"], "mes": row["mes"]},
            "cnpj_cpf": row["cnpj_cpf_digits"],
        }
    )


def _decision(row: Mapping[str, object], *, current: bool = False) -> Decision:
    return _DECISION.validate_python(
        {
            **{
                key: row[key]
                for key in (
                    "finding_key",
                    "decision",
                    "reviewer",
                    "comment",
                    "decided_at",
                )
            },
            "decision_id": str(row["decision_id"]),
            "run_id": str(row["decision_run_id" if current else "run_id"]),
            "step_id": str(row["decision_step_id" if current else "step_id"]),
        }
    )


def _current(row: Mapping[str, object]) -> Decision | None:
    return None if row["decision_id"] is None else _decision(row, current=True)


class DuckDBReviewRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def _rows(
        self, sql: str, parameters: list[object]
    ) -> tuple[dict[str, object], ...]:
        cursor = self.connection.execute(sql, parameters)
        names = [column[0] for column in cursor.description]
        return tuple(dict(zip(names, row, strict=True)) for row in cursor.fetchall())

    def list_deputies(self) -> tuple[DeputyOption, ...]:
        rows = self.connection.execute(
            """SELECT numero_deputado_id,
                      arg_max(nome_parlamentar, (ano, mes, nome_parlamentar)) AS name
               FROM despesas GROUP BY numero_deputado_id
               ORDER BY lower(name), numero_deputado_id"""
        ).fetchall()
        return tuple(DeputyOption(int(row[0]), str(row[1])) for row in rows)

    def list_pending(self, filters: ReviewFilters) -> tuple[ReviewItem, ...]:
        filters = replace(filters)
        rows = self._rows(
            _ORIGINAL_FINDINGS
            + _PENDING_WHERE
            + """
                ORDER BY CASE f.severity WHEN 'alta' THEN 0 WHEN 'media' THEN 1
                                        ELSE 2 END,
                         f.amount DESC, f.finding_key
                LIMIT ? OFFSET ?
            """,
            _pending_parameters(filters) + [filters.limit, filters.offset],
        )
        return tuple(ReviewItem(_record(row), _current(row)) for row in rows)

    def count_pending(self, filters: ReviewFilters) -> int:
        filters = replace(filters)
        row = self.connection.execute(
            "SELECT count(*) FROM ("
            + _ORIGINAL_FINDINGS
            + _PENDING_WHERE
            + ") pending",
            _pending_parameters(filters),
        ).fetchone()
        assert row is not None
        return int(row[0])

    def get_finding(self, key: str) -> ReviewDetail | None:
        rows = self._rows(_ORIGINAL_FINDINGS + " WHERE f.finding_key = ?", [key])
        if not rows:
            return None
        row = rows[0]
        record = _record(row)
        decisions = tuple(
            _decision(item)
            for item in self._rows(
                "SELECT * FROM decisions WHERE finding_key = ? "
                "ORDER BY decided_at, decision_id",
                [key],
            )
        )
        annotations = tuple(
            _ANNOTATION.validate_python(
                {
                    **item,
                    **{
                        name: str(item[name])
                        for name in (
                            "parecer_id",
                            "agent_trace_id",
                            "run_id",
                            "step_id",
                        )
                    },
                    "output": _object(item["output_json"]),
                }
            )
            for item in self._rows(
                "SELECT * FROM pareceres WHERE finding_key = ? "
                "ORDER BY created_at, parecer_id",
                [key],
            )
        )
        pdf = (
            DuckDBPdfRepository(self.connection).latest(
                record.finding.id_documento, record.url_documento
            )
            if record.url_documento is not None
            else None
        )
        return ReviewDetail(
            record, _current(row), _expense(row), decisions, annotations, pdf
        )

    def append_decision(self, decision: Decision) -> None:
        decision = replace(decision)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            active = self.connection.execute(
                "SELECT 1 FROM runs JOIN steps USING (run_id) "
                "WHERE runs.run_id = ? AND steps.step_id = ? "
                "AND runs.status = 'running' AND steps.status = 'running'",
                [decision.run_id, decision.step_id],
            ).fetchone()
            if active is None:
                raise RuntimeError(
                    "decision writes require a matching active run and step"
                )
            finding = self.connection.execute(
                "SELECT 1 FROM findings WHERE finding_key = ? AND status = 'active'",
                [decision.finding_key],
            ).fetchone()
            if finding is None:
                raise DomainError("decision requires an active finding")
            self.connection.execute(
                "INSERT INTO decisions "
                "(decision_id, finding_key, decision, reviewer, comment, "
                "decided_at, run_id, step_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    decision.decision_id,
                    decision.finding_key,
                    decision.decision.value,
                    decision.reviewer,
                    decision.comment,
                    decision.decided_at,
                    decision.run_id,
                    decision.step_id,
                ],
            )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

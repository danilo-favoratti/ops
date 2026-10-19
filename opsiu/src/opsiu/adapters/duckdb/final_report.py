"""Read approved findings and explanation history from one database snapshot."""

import json
from collections.abc import Iterator

from duckdb import DuckDBPyConnection
from pydantic import TypeAdapter

from opsiu.application.ports import FinalReportRecord, FindingExportRecord
from opsiu.domain.errors import DomainError

_FINAL = TypeAdapter(FinalReportRecord)


class DuckDBFinalReportReader:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def approved_findings(self) -> Iterator[FinalReportRecord]:
        # A single SELECT keeps decisions, original records and opinions in the
        # same MVCC snapshot, including while the consumer writes artifacts.
        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT f.*, s.path AS source_file, s.sha256 AS source_sha256,
                       s.size AS source_size_bytes,
                       d.* EXCLUDE (id_documento, source_file_id, record_sha256,
                                    run_id, step_id),
                       (
                           SELECT to_json(list(struct_pack(
                               parecer_id := CAST(p.parecer_id AS VARCHAR),
                               finding_key := p.finding_key,
                               agent_trace_id := CAST(p.agent_trace_id AS VARCHAR),
                               agent_id := p.agent_id, kind := p.kind,
                               model := p.model, prompt_version := p.prompt_version,
                               output := p.output_json,
                               created_at := strftime(
                                   p.created_at AT TIME ZONE 'UTC',
                                   '%Y-%m-%dT%H:%M:%S.%fZ'
                               ),
                               run_id := CAST(p.run_id AS VARCHAR),
                               step_id := CAST(p.step_id AS VARCHAR)
                           ) ORDER BY p.created_at, p.parecer_id))
                           FROM pareceres p
                           JOIN agent_traces t
                             ON t.agent_trace_id = p.agent_trace_id
                           WHERE p.finding_key = f.finding_key
                             AND p.kind = 'parecer' AND t.status = 'succeeded'
                       ) AS pareceres_json
                FROM findings_current f JOIN source_files s USING (source_file_id)
                LEFT JOIN LATERAL (
                    SELECT * FROM despesas
                    WHERE source_file_id = f.source_file_id
                      AND id_documento = f.id_documento
                      AND record_sha256 = f.record_sha256
                    ORDER BY source_record_number LIMIT 1
                ) d ON TRUE
                WHERE f.status = 'active' AND f.decision = 'aprovado'
                ORDER BY d.numero_deputado_id, f.rule_id, f.rule_version,
                         f.id_documento, f.finding_key
            """)
            names = [column[0] for column in cursor.description]
            while batch := cursor.fetchmany(1_000):
                for values in batch:
                    row = dict(zip(names, values, strict=True))
                    if row["source_record_number"] is None:
                        raise DomainError(
                            "finding has no matching original expense record"
                        )
                    finding = {
                        key: row[key]
                        for key in (
                            "rule_id",
                            "rule_version",
                            "id_documento",
                            "severity",
                            "amount",
                            "status",
                        )
                    }
                    finding["record_identity"] = row.get("record_identity", "")
                    finding["evidence"] = json.loads(row["evidence_json"])
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
                    result = _FINAL.validate_python(
                        {
                            "record": {
                                "finding": finding,
                                **{
                                    key: row[key]
                                    for key in FindingExportRecord.__dataclass_fields__
                                    if key != "finding"
                                },
                            },
                            "decision": {
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
                                "run_id": str(row["decision_run_id"]),
                                "step_id": str(row["decision_step_id"]),
                            },
                            "pareceres": json.loads(row["pareceres_json"] or "[]"),
                        }
                    )
                    if result.record.finding.finding_key != row["finding_key"]:
                        raise DomainError(
                            "stored finding identity does not match its key"
                        )
                    yield result
        finally:
            cursor.close()

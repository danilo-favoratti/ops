"""Read active findings joined to their immutable original expense snapshots."""

import json
from collections.abc import Iterator

from duckdb import DuckDBPyConnection
from pydantic import TypeAdapter

from opsiu.application.ports import FindingExportRecord
from opsiu.domain.errors import DomainError

_EXPORT_ADAPTER = TypeAdapter(FindingExportRecord)


class DuckDBFindingExportReader:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def active_findings(self) -> Iterator[FindingExportRecord]:
        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT f.*, s.path AS source_file, s.sha256 AS source_sha256,
                       s.size AS source_size_bytes,
                       d.* EXCLUDE (id_documento, source_file_id,
                                                record_sha256,
                                                run_id, step_id)
                FROM findings f JOIN source_files s USING (source_file_id)
                LEFT JOIN LATERAL (
                    SELECT * FROM despesas
                    WHERE source_file_id = f.source_file_id
                      AND id_documento = f.id_documento
                      AND record_sha256 = f.record_sha256
                    ORDER BY source_record_number LIMIT 1
                ) d ON TRUE
                WHERE f.status = 'active'
                ORDER BY f.rule_id, f.rule_version, f.id_documento, f.finding_key
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
                    export = {
                        key: row[key]
                        for key in FindingExportRecord.__dataclass_fields__
                        if key != "finding"
                    }
                    export["finding"] = finding
                    # Validate the adapter boundary before exposing a typed record.
                    record = _EXPORT_ADAPTER.validate_python(export)
                    if record.finding.finding_key != row["finding_key"]:
                        raise DomainError(
                            "stored finding identity does not match its key"
                        )
                    yield record
        finally:
            cursor.close()

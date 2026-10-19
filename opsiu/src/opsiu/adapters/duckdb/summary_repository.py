"""Stream original deputy evidence and append independently traceable summaries."""

import hashlib
import json
from collections.abc import Generator, Mapping
from datetime import datetime
from typing import cast

from agentrig.core import JsonValue as AgentJson
from agentrig.core import redact_private_trace_content
from duckdb import DuckDBPyConnection
from pydantic import TypeAdapter

from opsiu.application.consolidate import (
    summary_agent_input,
    summary_input_hash,
    summary_input_json,
)
from opsiu.application.ports import (
    AnnotationResult,
    DeputyReviewInput,
    JsonValue,
    SummaryFinding,
    SummarySpec,
)
from opsiu.domain.errors import DomainError

_ITEM = TypeAdapter(SummaryFinding)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(child) for child in value]
    return value


def _json(value: object) -> str:
    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, str):
        raise DomainError("summary evidence requires a stored JSON object")
    parsed: object = json.loads(value)
    if not isinstance(parsed, dict):
        raise DomainError("summary evidence requires a JSON object")
    _json(parsed)
    return cast(dict[str, JsonValue], parsed)


def _redacted_input(value: dict[str, JsonValue]) -> str:
    return _json(redact_private_trace_content(cast(Mapping[str, AgentJson], value)))


class DuckDBSummaryRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def inputs(
        self, model: str, *, deputy_id: int | None = None
    ) -> Generator[DeputyReviewInput, None, None]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                WITH opinions AS (
                    SELECT p.*, t.finding_key AS trace_finding_key,
                           t.agent_id AS trace_agent_id, t.model AS trace_model,
                           t.prompt_name AS trace_prompt_name,
                           t.prompt_version AS trace_prompt_version,
                           t.run_id AS trace_run_id, t.step_id AS trace_step_id,
                           t.output_json AS trace_output, t.failure_json
                    FROM pareceres p JOIN agent_traces t USING (agent_trace_id)
                    WHERE p.kind = 'parecer' AND p.model = ?
                      AND t.status = 'succeeded'
                    QUALIFY row_number() OVER (
                        PARTITION BY p.finding_key
                        ORDER BY p.created_at DESC, p.parecer_id DESC
                    ) = 1
                )
                SELECT f.*, s.path AS source_file, s.sha256 AS source_sha256,
                       d.numero_deputado_id, d.nome_parlamentar, d.ano, d.mes,
                       d.source_record_number,
                       p.parecer_id, p.agent_trace_id AS parecer_trace_id,
                       p.agent_id AS parecer_agent_id, p.model AS parecer_model,
                       p.prompt_version AS parecer_prompt_version,
                       p.run_id AS parecer_run_id, p.step_id AS parecer_step_id,
                       p.output_json AS parecer_json, p.trace_finding_key,
                       p.trace_agent_id, p.trace_model, p.trace_prompt_name,
                       p.trace_prompt_version, p.trace_run_id, p.trace_step_id,
                       p.trace_output, p.failure_json
                FROM findings f JOIN source_files s USING (source_file_id)
                JOIN opinions p USING (finding_key)
                LEFT JOIN LATERAL (
                    SELECT numero_deputado_id, nome_parlamentar, ano, mes,
                           source_record_number
                    FROM despesas
                    WHERE source_file_id = f.source_file_id
                      AND id_documento = f.id_documento
                      AND record_sha256 = f.record_sha256
                    ORDER BY source_record_number LIMIT 1
                ) d ON TRUE
                WHERE f.status = 'active' AND (
                    ? IS NULL OR d.numero_deputado_id = ?
                    OR d.source_record_number IS NULL
                )
                ORDER BY d.numero_deputado_id, f.finding_key
            """,
                [model, deputy_id, deputy_id],
            )
            names = [column[0] for column in cursor.description]
            group: list[SummaryFinding] = []
            current_id: int | None = None
            while batch := cursor.fetchmany(1_000):
                for values in batch:
                    item = self._item(dict(zip(names, values, strict=True)))
                    if group and current_id != item.numero_deputado_id:
                        yield self._group(group)
                        group = []
                    current_id = item.numero_deputado_id
                    group.append(item)
            if group:
                yield self._group(group)
        finally:
            cursor.close()

    @staticmethod
    def _group(items: list[SummaryFinding]) -> DeputyReviewInput:
        latest = max(
            items,
            key=lambda item: (
                item.ano,
                item.mes,
                item.finding.id_documento,
                item.finding.finding_key,
            ),
        )
        return DeputyReviewInput(
            latest.numero_deputado_id, latest.nome_parlamentar, tuple(items)
        )

    @staticmethod
    def _item(row: Mapping[str, object]) -> SummaryFinding:
        if row["source_record_number"] is None:
            raise DomainError("summary finding has no original expense record")
        if (
            row["trace_finding_key"] != row["finding_key"]
            or row["trace_agent_id"] != row["parecer_agent_id"]
            or row["parecer_agent_id"] != f"explicar-achado-{row['rule_id']}"
            or row["trace_model"] != row["parecer_model"]
            or row["trace_prompt_name"] != "explicar-achado"
            or row["trace_prompt_version"] != row["parecer_prompt_version"]
            or row["trace_run_id"] != row["parecer_run_id"]
            or row["trace_step_id"] != row["parecer_step_id"]
            or row["failure_json"] is not None
        ):
            raise DomainError("source opinion trace provenance conflicts")
        opinion = _object(row["parecer_json"])
        if _json(opinion) != _json(_object(row["trace_output"])):
            raise DomainError("source opinion output conflicts with its trace")
        finding: dict[str, object] = {
            key: row[key]
            for key in (
                "rule_id",
                "rule_version",
                "id_documento",
                "severity",
                "amount",
            )
        }
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
        item = _ITEM.validate_python(
            {
                "finding": finding,
                "numero_deputado_id": row["numero_deputado_id"],
                "nome_parlamentar": row["nome_parlamentar"],
                "ano": row["ano"],
                "mes": row["mes"],
                "parecer_id": str(row["parecer_id"]),
                "parecer_trace_id": str(row["parecer_trace_id"]),
                "parecer": opinion,
            }
        )
        if item.finding.finding_key != row["finding_key"]:
            raise DomainError("summary finding identity does not match its key")
        return item

    def has_success(self, deputy_id: int, input_sha256: str, spec: SummarySpec) -> bool:
        rows = self.connection.execute(
            "SELECT s.prompt_name, s.prompt_sha256, s.input_sha256, s.input_json, "
            "s.output_json, t.finding_key, t.agent_id, t.model, t.prompt_name, "
            "t.prompt_version, t.prompt_sha256, t.run_id, t.step_id, t.status, "
            "t.input_json, t.output_json, t.failure_json, s.run_id, s.step_id "
            "FROM resumos_deputado s JOIN agent_traces t USING (agent_trace_id) "
            "WHERE s.numero_deputado_id = ? AND s.model = ? AND s.prompt_version = ?",
            [deputy_id, spec.model, spec.prompt_version],
        ).fetchall()
        found = False
        for row in rows:
            if row[:2] != (spec.prompt_name, spec.prompt_sha256):
                raise DomainError("summary prompt changed without a version change")
            if row[2] != input_sha256:
                continue
            if (
                row[5:14]
                != (
                    None,
                    "consolidar-deputado",
                    spec.model,
                    spec.prompt_name,
                    spec.prompt_version,
                    spec.prompt_sha256,
                    row[17],
                    row[18],
                    "succeeded",
                )
                or row[16] is not None
            ):
                raise DomainError("stored summary trace provenance conflicts")
            stored = _object(row[3])
            if (
                hashlib.sha256(_json(stored).encode("utf-8")).hexdigest()
                != input_sha256
            ):
                raise DomainError("stored summary input hash conflicts")
            agent_input = stored.get("agent_input")
            if not isinstance(agent_input, dict):
                raise DomainError("stored summary has no agent input")
            if _redacted_input(agent_input) != _json(_object(row[14])):
                raise DomainError("stored summary input conflicts with its trace")
            if _json(_object(row[4])) != _json(_object(row[15])):
                raise DomainError("stored summary output conflicts with its trace")
            found = True
        return found

    def save(
        self,
        request: DeputyReviewInput,
        input_sha256: str,
        spec: SummarySpec,
        result: AnnotationResult,
        *,
        summary_id: str,
        created_at: datetime,
        run_id: str,
        step_id: str,
    ) -> None:
        active = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [run_id, step_id],
        ).fetchone()
        if active is None:
            raise RuntimeError("summary writes require a matching active run and step")
        if not request.findings or summary_input_hash(request) != input_sha256:
            raise DomainError("summary input hash does not match its supplied sources")
        current_inputs = self.inputs(spec.model, deputy_id=request.numero_deputado_id)
        try:
            current = next(current_inputs, None)
        finally:
            current_inputs.close()
        if current is None or summary_input_json(current) != summary_input_json(
            request
        ):
            raise DomainError(
                "summary sources are no longer the current eligible input"
            )
        if result.failure is not None or result.output is None:
            raise DomainError("only successful summary results can be saved")
        trace = self.connection.execute(
            "SELECT finding_key, agent_id, model, prompt_name, prompt_version, "
            "prompt_sha256, CAST(run_id AS VARCHAR), CAST(step_id AS VARCHAR), "
            "status, input_json, output_json, failure_json "
            "FROM agent_traces WHERE agent_trace_id = ?",
            [result.trace_id],
        ).fetchone()
        if (
            trace is None
            or trace[:9]
            != (
                None,
                "consolidar-deputado",
                spec.model,
                spec.prompt_name,
                spec.prompt_version,
                spec.prompt_sha256,
                run_id,
                step_id,
                "succeeded",
            )
            or trace[11] is not None
        ):
            raise DomainError("summary trace does not match successful invocation")
        if _redacted_input(summary_agent_input(request)) != _json(_object(trace[9])):
            raise DomainError("summary trace input does not match the supplied sources")
        output = _json(result.output)
        if output != _json(_object(trace[10])):
            raise DomainError("summary output does not match its private trace")
        self.has_success(request.numero_deputado_id, input_sha256, spec)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute(
                "INSERT INTO resumos_deputado VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    summary_id,
                    request.numero_deputado_id,
                    request.nome_parlamentar,
                    result.trace_id,
                    spec.model,
                    spec.prompt_name,
                    spec.prompt_version,
                    spec.prompt_sha256,
                    input_sha256,
                    summary_input_json(request),
                    output,
                    created_at,
                    run_id,
                    step_id,
                ],
            )
            for item in request.findings:
                origin = item.finding.provenance.source
                self.connection.execute(
                    "INSERT INTO resumo_fontes VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        summary_id,
                        item.finding.finding_key,
                        item.parecer_id,
                        item.parecer_trace_id,
                        origin.source_file_id,
                        origin.source_sha256,
                        origin.record_sha256,
                    ],
                )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

"""Append annotations only when their successful private trace proves origin."""

import json
from datetime import datetime

from duckdb import DuckDBPyConnection

from opsiu.application.ports import AnnotationResult, AnnotationSpec
from opsiu.domain.errors import DomainError


def _output_json(value: object) -> str:
    if not isinstance(value, dict):
        raise DomainError("annotation output must be a JSON object")
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise DomainError("annotation output is not valid JSON") from error


def _stored_output(value: object) -> str:
    if not isinstance(value, str):
        raise DomainError("successful annotation trace has no JSON output")
    try:
        decoded: object = json.loads(value)
    except ValueError as error:
        raise DomainError("stored annotation output is not valid JSON") from error
    return _output_json(decoded)


class DuckDBAnnotationRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def has_success(self, finding_key: str, spec: AnnotationSpec) -> bool:
        rows = self.connection.execute(
            "SELECT t.prompt_name, t.prompt_sha256, t.finding_key, t.agent_id, "
            "t.prompt_version, t.model, CAST(t.run_id AS VARCHAR), "
            "CAST(t.step_id AS VARCHAR), t.output_json, t.failure_json, "
            "CAST(p.run_id AS VARCHAR), CAST(p.step_id AS VARCHAR), p.output_json "
            "FROM pareceres p JOIN agent_traces t USING (agent_trace_id) "
            "JOIN findings f ON f.finding_key = p.finding_key "
            "WHERE f.status = 'active' AND p.finding_key = ? "
            "AND p.agent_id = ? AND p.kind = ? AND p.prompt_version = ? "
            "AND p.model = ? AND t.status = 'succeeded'",
            [finding_key, spec.agent_id, spec.kind, spec.prompt_version, spec.model],
        ).fetchall()
        for row in rows:
            if row[:2] != (spec.prompt_name, spec.prompt_sha256):
                raise DomainError("annotation prompt changed without a version change")
            if (
                row[2:8]
                != (
                    finding_key,
                    spec.agent_id,
                    spec.prompt_version,
                    spec.model,
                    row[10],
                    row[11],
                )
                or row[9] is not None
            ):
                raise DomainError("stored annotation trace provenance conflicts")
            if _stored_output(row[8]) != _stored_output(row[12]):
                raise DomainError("stored annotation output conflicts with its trace")
        return bool(rows)

    def save(
        self,
        finding_key: str,
        spec: AnnotationSpec,
        result: AnnotationResult,
        *,
        parecer_id: str,
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
            raise RuntimeError(
                "annotation writes require a matching active run and step"
            )
        finding = self.connection.execute(
            "SELECT 1 FROM findings WHERE finding_key = ? AND status = 'active'",
            [finding_key],
        ).fetchone()
        if finding is None:
            raise DomainError("annotation requires an active finding")
        if result.failure is not None or result.output is None:
            raise DomainError("only successful annotation results can be saved")
        output = _output_json(result.output)
        trace = self.connection.execute(
            "SELECT finding_key, CAST(run_id AS VARCHAR), CAST(step_id AS VARCHAR), "
            "agent_id, model, prompt_name, prompt_version, prompt_sha256, status, "
            "output_json, failure_json FROM agent_traces WHERE agent_trace_id = ?",
            [result.trace_id],
        ).fetchone()
        expected = (
            finding_key,
            run_id,
            step_id,
            spec.agent_id,
            spec.model,
            spec.prompt_name,
            spec.prompt_version,
            spec.prompt_sha256,
            "succeeded",
        )
        if trace is None or trace[:9] != expected or trace[10] is not None:
            raise DomainError("annotation trace does not match successful invocation")
        if _stored_output(trace[9]) != output:
            raise DomainError("annotation output does not match its private trace")
        # Force appends another annotation, but cannot rewrite a prompt version.
        self.has_success(finding_key, spec)
        self.connection.execute(
            "INSERT INTO pareceres VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                parecer_id,
                finding_key,
                result.trace_id,
                spec.agent_id,
                spec.kind,
                spec.prompt_version,
                spec.model,
                output,
                created_at,
                run_id,
                step_id,
            ],
        )

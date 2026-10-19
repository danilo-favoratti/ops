"""Append-only private agent auditing and a deliberately narrow public event bridge."""

import asyncio
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar, cast
from uuid import UUID

from agentrig.agents import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentResult,
    AgentRuntime,
    ConfiguredAgent,
)
from agentrig.core import (
    AgentRigError,
    CancellationSource,
    Deadline,
    Event,
    EventId,
    Failure,
    FailureKind,
    JsonValue,
    PrivateTraceCapture,
    PrivateTraceRecord,
    RunContext,
    RunId,
    normalize_exception,
    redact_private_trace_content,
)
from duckdb import DuckDBPyConnection

from opsiu.adapters.agents.prompts import Prompt
from opsiu.application.ports import EventRecord
from opsiu.application.ports import JsonValue as LedgerJson
from opsiu.application.runs import Run

_STRING_LIMIT = 4 * 1024 * 1024
_CHUNK_CHARACTERS = 64 * 1024
_SAFE_ATTRIBUTES = frozenset(
    {
        "agent_id",
        "agent_version",
        "provider",
        "model",
        "input_schema",
        "output_schema",
        "prompt_version",
        "call_index",
        "status",
        "failure_kind",
        "failure_code",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "turn_count",
        "tool_call_count",
    }
)
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
IdentityT = TypeVar("IdentityT")


def _value(value: object) -> JsonValue:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _value(getattr(value, field.name)) for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_value(child) for child in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (Decimal, date)):
        return str(value)
    return {"unsupported_type": type(value).__name__}


def _json(value: object) -> str:
    return json.dumps(
        _value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _redact(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], _value(redact_private_trace_content(value)))


def _check_size(value: JsonValue) -> None:
    if isinstance(value, str) and len(value.encode("utf-8")) > _STRING_LIMIT:
        raise AgentRigError(
            Failure(
                kind=FailureKind.INVALID_INPUT,
                code="agent.trace_content_too_large",
                message="An individual private trace string exceeds the audit limit",
            )
        )
    if isinstance(value, Mapping):
        for child in value.values():
            _check_size(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_size(child)


@dataclass(frozen=True)
class TracedAgentResult(Generic[OutputT]):
    trace_id: str
    result: AgentResult[OutputT]


class DuckDBTraceRecorder:
    """Persist SDK private records without forwarding their content to events."""

    def __init__(self, connection: DuckDBPyConnection, run: Run) -> None:
        self.connection = connection
        self.run = run

    def require_step(self) -> str:
        step_id = self.run.require_step()
        active = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [self.run.run_id, step_id],
        ).fetchone()
        if active is None:
            raise RuntimeError("agent tracing requires a persisted active run and step")
        return step_id

    def record(self, record: PrivateTraceRecord) -> None:
        step_id = self.require_step()
        trace_id = record.correlation.get("agent_trace_id")
        if (
            str(record.run_id) != self.run.run_id
            or record.correlation.get("step_id") != step_id
            or trace_id is None
        ):
            raise ValueError(
                "private trace lineage does not match the active invocation"
            )
        UUID(trace_id)
        self.connection.execute(
            "INSERT INTO agent_trace_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                record.record_id,
                trace_id,
                self.run.run_id,
                step_id,
                record.kind,
                record.content,
                record.content_bytes,
                record.truncated,
                _json(record.metadata),
                _json(record.correlation),
                record.occurred_at,
            ],
        )

    def capture(
        self,
        context: RunContext,
        *,
        kind: str,
        content: Mapping[str, JsonValue],
        metadata: Mapping[str, str],
    ) -> dict[str, JsonValue]:
        redacted = _redact(content)
        encoded = _json(redacted)
        count = max(1, math.ceil(len(encoded) / _CHUNK_CHARACTERS))
        for index in range(count):
            chunk = _json(
                {
                    "part_index": index,
                    "total_parts": count,
                    "payload_chunk": encoded[
                        index * _CHUNK_CHARACTERS : (index + 1) * _CHUNK_CHARACTERS
                    ],
                }
            )
            self.record(
                PrivateTraceRecord(
                    record_id=self.run.ids.new_id(),
                    kind=kind,
                    occurred_at=self.run.clock.now(),
                    run_id=context.run_id,
                    parent_run_id=context.parent_run_id,
                    content=chunk,
                    content_bytes=len(chunk.encode("utf-8")),
                    truncated=False,
                    metadata=metadata,
                    correlation=context.correlation,
                )
            )
        return redacted


class RunEventSink:
    """Keep SDK lineage, IDs, and timing while discarding unbounded public text."""

    def __init__(self, run: Run, trace_id: str, step_id: str) -> None:
        self.run = run
        self.trace_id = trace_id
        self.step_id = step_id

    def emit(self, event: Event) -> None:
        if (
            self.run.require_step() != self.step_id
            or str(event.run_id) != self.run.run_id
            or event.correlation.get("step_id") != self.step_id
            or event.correlation.get("agent_trace_id") != self.trace_id
        ):
            raise ValueError("AgentRig event lineage does not match the active step")
        safe = {
            key: value
            for key, value in event.attributes.items()
            if key in _SAFE_ATTRIBUTES
            and (value is None or type(value) in {str, int, float, bool})
        }
        attributes = _redact({"agent_trace_id": self.trace_id, **safe})
        self.run.ledger.emit(
            EventRecord(
                str(event.event_id),
                self.run.run_id,
                self.step_id,
                f"agentrig.{event.kind.value}",
                cast(dict[str, LedgerJson], attributes),
                event.occurred_at,
            )
        )


class _Clock:
    def __init__(self, run: Run) -> None:
        self.run = run

    def now(self) -> datetime:
        return self.run.clock.now()

    def monotonic(self) -> float:
        return time.monotonic()


class _Ids(Generic[IdentityT]):
    def __init__(self, run: Run, factory: Callable[[str], IdentityT]) -> None:
        self.run = run
        self.factory = factory

    def generate(self) -> IdentityT:
        return self.factory(self.run.ids.new_id())


class _CapturedRuntime:
    def __init__(
        self,
        runtime: AgentRuntime,
        recorder: DuckDBTraceRecorder,
        metadata: Mapping[str, str],
    ) -> None:
        self.runtime = runtime
        self.recorder = recorder
        self.metadata = metadata
        self.execution: AgentExecutionResult | None = None
        self.response: dict[str, JsonValue] | None = None

    async def execute(
        self, request: AgentExecutionRequest, context: RunContext
    ) -> AgentExecutionResult:
        try:
            execution = await self.runtime.execute(request, context)
        except (Exception, asyncio.CancelledError) as error:
            execution = AgentExecutionResult.from_failure(normalize_exception(error))
        self.execution = execution
        content: dict[str, JsonValue] = {
            "output": execution.result.output,
            "failure": _value(execution.result.failure),
            "status": execution.result.status.value,
            "usage": _value(execution.usage),
            "provider_metadata": _value(execution.provider_metadata),
        }
        self.response = self.recorder.capture(
            context, kind="provider.response", content=content, metadata=self.metadata
        )
        _check_size(content)
        return execution


class TracedAgentInvoker:
    """Record one complete invocation, including its final typed-codec outcome."""

    def __init__(
        self,
        connection: DuckDBPyConnection,
        run: Run,
        *,
        model: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not model.strip() or model != model.strip():
            raise ValueError("agent model must be nonempty trimmed text")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("agent timeout must be finite and positive")
        self.connection = connection
        self.run = run
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.recorder = DuckDBTraceRecorder(connection, run)

    async def invoke(
        self,
        agent: ConfiguredAgent[InputT, OutputT],
        request: InputT,
        *,
        prompt: Prompt,
        finding_key: str | None = None,
    ) -> TracedAgentResult[OutputT]:
        step_id = self.recorder.require_step()
        if (
            agent.instructions != prompt.text
            or agent.contract.prompt_version != prompt.version
            or re.fullmatch(r"[0-9a-f]{64}", prompt.sha256) is None
        ):
            raise ValueError("prompt metadata does not match the configured agent")
        if (
            finding_key is not None
            and self.connection.execute(
                "SELECT 1 FROM findings WHERE finding_key = ?", [finding_key]
            ).fetchone()
            is None
        ):
            raise ValueError("agent finding key does not exist")
        trace_id = self.run.ids.new_id()
        clock = _Clock(self.run)
        context = RunContext(
            run_id=RunId(self.run.run_id),
            parent_run_id=None,
            clock=clock,
            id_generator=_Ids(self.run, RunId),
            event_id_generator=_Ids(self.run, EventId),
            cancellation=CancellationSource().token,
            event_sink=RunEventSink(self.run, trace_id, step_id),
            deadline=Deadline.after(self.timeout_seconds, clock),
            correlation={"step_id": step_id, "agent_trace_id": trace_id},
            private_trace_capture=PrivateTraceCapture(recorder=self.recorder),
        )
        metadata = {
            "model": self.model,
            "agent_id": agent.contract.agent_id,
            "prompt_version": prompt.version,
            "output_schema": agent.contract.output_schema,
        }
        runtime = _CapturedRuntime(agent.runtime, self.recorder, metadata)
        captured: dict[str, JsonValue] = {
            "instructions": agent.instructions,
            "input": _value(request),
            "provider_options": _value(agent.provider_options),
        }
        started_at = clock.now()
        started = clock.monotonic()
        result: AgentResult[OutputT]
        redacted = self.recorder.capture(
            context, kind="provider.request", content=captured, metadata=metadata
        )
        try:
            _check_size(captured)
            async with asyncio.timeout(self.timeout_seconds):
                result = await replace(agent, runtime=runtime).run(request, context)
        except (Exception, asyncio.CancelledError) as error:
            result = AgentResult.from_failure(normalize_exception(error))
        finished_at = clock.now()
        elapsed = max(0.0, (clock.monotonic() - started) * 1000)
        failure = _redact({"failure": _value(result.failure)})["failure"]
        response = runtime.response or {}
        usage = asdict(runtime.execution.usage) if runtime.execution is not None else {}
        self.recorder.require_step()
        self.connection.execute(
            "INSERT INTO agent_traces VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                trace_id,
                self.run.run_id,
                step_id,
                finding_key,
                agent.contract.agent_id,
                agent.contract.version,
                self.model,
                prompt.name,
                prompt.version,
                prompt.sha256,
                agent.contract.input_schema,
                agent.contract.output_schema,
                redacted["instructions"],
                _json(redacted["input"]),
                _json(response.get("output")) if runtime.response is not None else None,
                _json(usage),
                _json(failure) if failure is not None else None,
                result.status.value,
                started_at,
                finished_at,
                elapsed,
            ],
        )
        self.run.metadata["model"] = self.model
        versions = self.run.metadata.setdefault("prompt_versions", {})
        hashes = self.run.metadata.setdefault("prompt_hashes", {})
        assert isinstance(versions, dict) and isinstance(hashes, dict)
        versions[prompt.name] = prompt.version
        hashes[prompt.name] = prompt.sha256
        self.run.counters["agent_calls"] = self.run.counters.get("agent_calls", 0) + 1
        self.run.emit(
            "agent.completed",
            {
                "agent_trace_id": trace_id,
                "agent_id": agent.contract.agent_id,
                "status": result.status.value,
            },
        )
        return TracedAgentResult(trace_id, result)

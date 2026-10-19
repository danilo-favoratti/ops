"""Private trace chunking, redaction, and failure boundaries without providers."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from agentrig.agents import (
    AgentContract,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentLimits,
    AgentRuntime,
    ConfiguredAgent,
)
from agentrig.core import EffectProfile, JsonValue, RunContext
from duckdb import DuckDBPyConnection

from opsiu.adapters.agents.contracts import Parecer, ParecerCodec
from opsiu.adapters.agents.prompts import load_prompt
from opsiu.adapters.agents.runtimes import ScriptedAgentScenario, build_scripted_runtime
from opsiu.adapters.agents.trace import TracedAgentInvoker
from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.runs import Run

pytestmark = pytest.mark.unit


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[tuple[DuckDBPyConnection, Run]]:
    with connect_database(tmp_path / "db.duckdb") as connection:
        apply_migrations(connection)
        run = Run(
            ledger=CompositeLedger(
                JsonlLedger(tmp_path / "runs"), DuckDBLedger(connection)
            ),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="trace unit",
            arguments={},
            metadata=collect_metadata("settings", tmp_path),
        )
        yield connection, run


class InputCodec:
    schema_id = "test.input"

    def encode(self, value: str) -> JsonValue:
        if value == "INVALID_INPUT":
            raise ValueError("synthetic invalid input")
        return value


def configured(runtime: AgentRuntime) -> ConfiguredAgent[str, Parecer]:
    prompt = load_prompt("explicar-achado")
    return ConfiguredAgent(
        runtime=runtime,
        contract=AgentContract(
            agent_id="unit.trace",
            version="1",
            purpose="Test private capture",
            input_schema="test.input",
            output_schema=ParecerCodec().schema_id,
            prompt_version=prompt.version,
            effect_profile=EffectProfile.READ_ONLY,
            limits=AgentLimits(max_turns=1, max_tool_calls=0),
            stopping_policy="output_schema_satisfied",
            allowed_capabilities=("test",),
            permissions={"workspace": "read_only", "network": "denied"},
        ),
        instructions=prompt.text,
        input_codec=InputCodec(),
        output_codec=ParecerCodec(),
    )


def success(fundamento: str = "Revisão humana necessária.") -> AgentExecutionResult:
    return AgentExecutionResult.succeeded(
        {
            "fundamento": fundamento,
            "confianca": "baixa",
            "evidencias_citadas": ["Registro recebido."],
            "recomendacao": "revisar",
        }
    )


def test_private_chunks_reassemble_complete_unicode_and_apply_consistent_redaction(
    workspace: tuple[DuckDBPyConnection, Run],
) -> None:
    connection, run = workspace
    runtime = build_scripted_runtime(
        [ScriptedAgentScenario(result=success("token=synthetic-secret RESPOSTA"))]
    )
    prompt = load_prompt("explicar-achado")
    text = "EVIDÊNCIA " * 25_000 + " password=synthetic-password FINAL"
    with run:
        with run.step("annotate"):
            result = asyncio.run(
                TracedAgentInvoker(connection, run, model="scripted").invoke(
                    configured(runtime), text, prompt=prompt
                )
            )
    assert result.result.is_success
    row = connection.execute(
        "SELECT input_json, output_json FROM agent_traces WHERE agent_trace_id = ?",
        [result.trace_id],
    ).fetchone()
    assert row is not None
    assert "synthetic-password" not in row[0]
    assert "synthetic-secret" not in row[1]
    assert "EVIDÊNCIA" in row[0] and "FINAL" in row[0]
    assert "RESPOSTA" in row[1]
    records = connection.execute(
        "SELECT content, content_bytes, truncated FROM agent_trace_records "
        "WHERE agent_trace_id = ? AND kind = 'provider.request'",
        [result.trace_id],
    ).fetchall()
    assert len(records) > 1
    decoded = []
    for content, size, truncated in records:
        assert not truncated
        assert size == len(content.encode("utf-8")) < 4 * 1024 * 1024
        assert "synthetic-password" not in content
        decoded.append(json.loads(content))
    combined = json.loads(
        "".join(
            item["payload_chunk"]
            for item in sorted(decoded, key=lambda item: item["part_index"])
        )
    )
    assert combined["input"] == json.loads(row[0])
    assert all(item["total_parts"] == len(records) for item in decoded)


def test_invalid_input_is_traced_without_consuming_a_runtime_scenario(
    workspace: tuple[DuckDBPyConnection, Run],
) -> None:
    connection, run = workspace
    runtime = build_scripted_runtime([ScriptedAgentScenario(result=success())])
    with run:
        with run.step("invalid"):
            result = asyncio.run(
                TracedAgentInvoker(connection, run, model="scripted").invoke(
                    configured(runtime),
                    "INVALID_INPUT",
                    prompt=load_prompt("explicar-achado"),
                )
            )
    assert not result.result.is_success
    assert runtime.calls == ()
    row = connection.execute(
        "SELECT status, input_json, failure_json FROM agent_traces"
    ).fetchone()
    assert row is not None and row[0] == "failed"
    assert json.loads(row[1]) == "INVALID_INPUT"
    assert json.loads(row[2])["code"] == "agent.input_schema_mismatch"


def test_oversized_request_fails_explicitly_before_provider_execution(
    workspace: tuple[DuckDBPyConnection, Run],
) -> None:
    connection, run = workspace
    runtime = build_scripted_runtime([ScriptedAgentScenario(result=success())])
    with run:
        with run.step("oversized"):
            result = asyncio.run(
                TracedAgentInvoker(connection, run, model="scripted").invoke(
                    configured(runtime),
                    "x" * (4 * 1024 * 1024 + 1),
                    prompt=load_prompt("explicar-achado"),
                )
            )
    assert not result.result.is_success
    assert runtime.calls == ()
    assert result.result.failure is not None
    assert result.result.failure.code == "agent.trace_content_too_large"
    assert connection.execute("SELECT status FROM agent_traces").fetchone() == (
        "failed",
    )


class ThrowingRuntime:
    async def execute(
        self, request: AgentExecutionRequest, context: RunContext
    ) -> AgentExecutionResult:
        raise ValueError("password=synthetic-secret provider failed")


def test_runtime_exception_is_a_private_redacted_terminal_failure(
    workspace: tuple[DuckDBPyConnection, Run],
) -> None:
    connection, run = workspace
    with run:
        with run.step("exception"):
            result = asyncio.run(
                TracedAgentInvoker(connection, run, model="scripted").invoke(
                    configured(ThrowingRuntime()),
                    "ordinary input",
                    prompt=load_prompt("explicar-achado"),
                )
            )
    assert not result.result.is_success
    failure = connection.execute("SELECT failure_json FROM agent_traces").fetchone()
    assert failure is not None and "synthetic-secret" not in failure[0]
    records = connection.execute("SELECT content FROM agent_trace_records").fetchall()
    assert all("synthetic-secret" not in record[0] for record in records)


def test_invocations_require_active_persisted_steps_and_matching_prompt(
    workspace: tuple[DuckDBPyConnection, Run],
) -> None:
    connection, run = workspace
    runtime = build_scripted_runtime([ScriptedAgentScenario(result=success())])
    invoker = TracedAgentInvoker(connection, run, model="scripted")
    agent = configured(runtime)
    prompt = load_prompt("explicar-achado")
    with pytest.raises(RuntimeError, match="not active"):
        asyncio.run(invoker.invoke(agent, "text", prompt=prompt))
    with run:
        with pytest.raises(RuntimeError, match="active run step"):
            asyncio.run(invoker.invoke(agent, "text", prompt=prompt))
        with run.step("bad metadata"):
            with pytest.raises(ValueError, match="prompt metadata"):
                asyncio.run(
                    invoker.invoke(agent, "text", prompt=load_prompt("revisar-nota"))
                )
            with pytest.raises(ValueError, match="finding key"):
                asyncio.run(
                    invoker.invoke(agent, "text", prompt=prompt, finding_key="missing")
                )
    assert runtime.calls == ()
    assert connection.execute("SELECT COUNT(*) FROM agent_traces").fetchone() == (0,)

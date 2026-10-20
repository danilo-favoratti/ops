"""Agent gateway inputs preserve source facts; offline outputs stay simulated."""

import asyncio
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TypeVar, cast

import pytest
from agentrig.agents import AgentExecutionResult, AgentRuntime, ConfiguredAgent
from agentrig.core import (
    CancellationSource,
    EventId,
    Failure,
    FailureKind,
    InMemoryEventSink,
    RunContext,
    RunId,
    Uuid4IdGenerator,
)
from agentrig.core import (
    JsonValue as AgentJson,
)
from agentrig.core import (
    SystemClock as AgentClock,
)
from duckdb import DuckDBPyConnection

from opsiu.adapters.agents import gateway
from opsiu.adapters.agents.gateway import AgentRigGateway
from opsiu.adapters.agents.prompts import Prompt, default_llms_directory
from opsiu.adapters.agents.runtimes import (
    ScriptedAgentScenario,
    build_scripted_runtime,
)
from opsiu.adapters.agents.trace import TracedAgentResult
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator
from opsiu.application.ports import (
    AnnotationRequest,
    FindingExportRecord,
    PdfContent,
    PdfExtraction,
    RunMetadata,
)
from opsiu.application.runs import Run
from opsiu.domain.findings import Finding, Provenance, SourceOrigin
from opsiu.settings import AgentSettings, Settings

pytestmark = pytest.mark.unit
InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Invocation:
    agent_id: str
    model: str
    encoded_input: AgentJson
    finding_key: str | None
    prompt: Prompt


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def invocations(monkeypatch: pytest.MonkeyPatch) -> list[Invocation]:
    calls: list[Invocation] = []

    class Invoker:
        def __init__(
            self, connection: DuckDBPyConnection, run: Run, *, model: str
        ) -> None:
            self.run = run
            self.model = model

        async def invoke(
            self,
            agent: ConfiguredAgent[InputT, OutputT],
            request: InputT,
            *,
            prompt: Prompt,
            finding_key: str | None = None,
        ) -> TracedAgentResult[OutputT]:
            self.run.require_step()
            calls.append(
                Invocation(
                    agent.contract.agent_id,
                    self.model,
                    agent.input_codec.encode(request),
                    finding_key,
                    prompt,
                )
            )
            context = RunContext.create_root(
                clock=AgentClock(),
                id_generator=Uuid4IdGenerator(RunId),
                cancellation=CancellationSource().token,
                event_sink=InMemoryEventSink(),
                event_id_generator=Uuid4IdGenerator(EventId),
            )
            result = await agent.run(request, context)
            return TracedAgentResult(f"trace-{len(calls)}", result)

    monkeypatch.setattr(gateway, "TracedAgentInvoker", Invoker)
    return calls


def make_run(tmp_path: Path) -> Run:
    return Run(
        ledger=JsonlLedger(tmp_path / "runs"),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="explain",
        arguments={},
        metadata=RunMetadata("settings", "commit", False, "3.12", "0.1.0"),
    )


def make_request(
    *, text: str | None = None, needs_ocr: bool = False
) -> AnnotationRequest:
    finding = Finding(
        "R08",
        "1",
        42,
        "alta",
        Decimal("90071992547409.91"),
        {"valor": Decimal("90071992547409.91"), "documentos": [42]},
        Provenance(
            "original-run",
            "original-step",
            SourceOrigin("source-id", "original.json", "a" * 64, "b" * 64),
            "original-commit",
            NOW,
        ),
    )
    record = FindingExportRecord(
        finding,
        101,
        "Deputada Original",
        "DF",
        None,
        2026,
        1,
        "Fornecedor Original",
        "00000000000001",
        "000042",
        date(2026, 1, 1),
        Decimal("90071992547409.92"),
        Decimal("0.01"),
        Decimal("90071992547409.91"),
        "https://example.invalid/original.pdf",
        7,
        12345,
    )
    pdf = (
        None
        if text is None
        else PdfExtraction(
            "extraction-id",
            PdfContent(
                42,
                "https://example.invalid/original.pdf",
                "snapshot-id",
                "c" * 64,
                Path("original.pdf"),
                "c" * 64,
                1000,
                "pypdf",
                "test-version",
                text,
                hashlib.sha256(text.encode()).hexdigest(),
                needs_ocr,
                1,
            ),
            NOW,
            "pdf-run",
            "pdf-step",
        )
    )
    return AnnotationRequest(record, "Regra original em português.", pdf)


def make_gateway(run: Run, *, budget: int = 20, live: bool = False) -> AgentRigGateway:
    return AgentRigGateway(
        cast(DuckDBPyConnection, object()),
        run,
        Settings(
            agents=AgentSettings(model="synthetic-live-model", max_calls_per_run=budget)
        ),
        live=live,
    )


def test_default_remains_offline_with_exact_original_context_and_fixture_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocations: list[Invocation]
) -> None:
    def forbidden_live(*arguments: object, **keywords: object) -> AgentRuntime:
        pytest.fail("default gateway must not construct a live runtime")

    monkeypatch.setattr(gateway, "build_codex_runtime", forbidden_live)
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    run = make_run(tmp_path)
    adapter = make_gateway(run)
    spec = adapter.spec("parecer", "R08")
    assert spec.agent_id == "explicar-achado-R08" and spec.model == "scripted"
    assert invocations == [] and "scripted_scenarios" not in run.metadata
    request = make_request(text="Texto original do PDF.")
    with run:
        with run.step("annotate"):
            result = asyncio.run(adapter.annotate(request, kind="parecer"))
    assert result.failure is None and result.output is not None
    assert result.output["confianca"] == "baixa"
    assert result.output["recomendacao"] == "revisar"
    assert "simulada" in str(result.output["fundamento"])
    call = invocations[0]
    assert call.agent_id == spec.agent_id and call.model == spec.model
    assert call.finding_key == request.record.finding.finding_key
    assert call.prompt.sha256 == spec.prompt_sha256
    assert isinstance(call.encoded_input, dict)
    source = call.encoded_input["registro"]
    finding = call.encoded_input["achado"]
    assert isinstance(source, dict) and isinstance(finding, dict)
    assert source["nome_parlamentar"] == "Deputada Original"
    assert source["numero"] == "000042"
    assert source["cnpj_cpf"] == "00000000000001"
    assert source["data_emissao"] == "2026-01-01"
    assert source["valor_documento"] == "90071992547409.92"
    assert source["valor_glosa"] == "0.01"
    assert source["valor_liquido"] == "90071992547409.91"
    assert finding["source_sha256"] == "a" * 64
    assert finding["record_sha256"] == "b" * 64
    assert (
        finding["evidencia_json"] == '{"documentos":[42],"valor":"90071992547409.91"}'
    )
    assert call.encoded_input["texto_nota"] == "Texto original do PDF."
    raw = (default_llms_directory() / "scenarios/annotations-v1.json").read_bytes()
    assert run.metadata["scripted_scenarios"] == {
        "annotations-v1.json": {
            "version": "1",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    }


def test_offline_invoice_returns_unknown_fields_without_fabricating_pdf_facts(
    tmp_path: Path, invocations: list[Invocation]
) -> None:
    run = make_run(tmp_path)
    adapter = make_gateway(run)
    request = make_request(text="Emitente: Empresa conhecida. Valor: 125,00.")
    with run:
        with run.step("invoice"):
            result = asyncio.run(adapter.annotate(request, kind="nota_fiscal"))
    assert result.failure is None and result.output is not None
    assert all(
        result.output[field] is None
        for field in ("emitente", "cnpj", "data", "valor", "descricao")
    )
    assert result.output["divergencias"] == []
    assert "simulada" in str(result.output["limitacoes"])
    assert invocations[0].agent_id == "revisar-nota"
    assert isinstance(invocations[0].encoded_input, dict)
    assert request.pdf is not None
    assert invocations[0].encoded_input["texto_pdf"] == request.pdf.content.text


@pytest.mark.parametrize(
    "text,needs_ocr", [(None, False), (" \n\t", False), ("fragmento", True)]
)
def test_unusable_pdf_is_omitted_from_opinion_and_rejected_for_invoice(
    tmp_path: Path,
    invocations: list[Invocation],
    text: str | None,
    needs_ocr: bool,
) -> None:
    run = make_run(tmp_path)
    adapter = make_gateway(run)
    request = make_request(text=text, needs_ocr=needs_ocr)
    with run:
        with run.step("annotate"):
            result = asyncio.run(adapter.annotate(request, kind="parecer"))
            with pytest.raises(ValueError, match="usable text"):
                asyncio.run(adapter.annotate(request, kind="nota_fiscal"))
    assert result.failure is None and len(invocations) == 1
    assert isinstance(invocations[0].encoded_input, dict)
    assert invocations[0].encoded_input["texto_nota"] is None


@pytest.mark.parametrize("mismatch", ["document", "url"])
def test_pdf_context_must_match_original_document_before_invocation(
    tmp_path: Path, invocations: list[Invocation], mismatch: str
) -> None:
    run = make_run(tmp_path)
    adapter = make_gateway(run)
    request = make_request(text="Texto legível.")
    assert request.pdf is not None
    content = request.pdf.content
    wrong = (
        replace(content, id_documento=43)
        if mismatch == "document"
        else replace(content, url="https://example.invalid/other.pdf")
    )
    request = replace(request, pdf=replace(request.pdf, content=wrong))
    with run:
        with run.step("mismatch"):
            with pytest.raises(ValueError, match="original expense"):
                asyncio.run(adapter.annotate(request, kind="parecer"))
    assert invocations == [] and "scripted_scenarios" not in run.metadata


def test_separate_finite_runtimes_exhaust_without_repeating_scenarios(
    tmp_path: Path, invocations: list[Invocation]
) -> None:
    run = make_run(tmp_path)
    adapter = make_gateway(run, budget=1)
    request = make_request(text="Texto legível.")
    with run:
        with run.step("finite"):
            first = asyncio.run(adapter.annotate(request, kind="parecer"))
            second = asyncio.run(adapter.annotate(request, kind="parecer"))
            invoice = asyncio.run(adapter.annotate(request, kind="nota_fiscal"))
    assert first.output is not None and invoice.output is not None
    assert second.output is None and second.failure is not None
    assert second.failure["code"] == "scripted_agent_runtime.exhausted"
    assert len(invocations) == 3


def test_live_gateway_uses_configured_model_and_lazy_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocations: list[Invocation]
) -> None:
    calls: list[tuple[Settings, Path | None]] = []

    def fake_live(settings: Settings, *, repo_root: Path | None = None) -> AgentRuntime:
        calls.append((settings, repo_root))
        return build_scripted_runtime(
            ScriptedAgentScenario(
                result=AgentExecutionResult.succeeded(
                    {
                        "fundamento": "Saída sintética do adaptador de teste.",
                        "confianca": "baixa",
                        "evidencias_citadas": ["Teste local."],
                        "recomendacao": "revisar",
                    }
                )
            )
            for _ in range(2)
        )

    monkeypatch.setattr(gateway, "build_codex_runtime", fake_live)
    run = make_run(tmp_path)
    adapter = make_gateway(run, live=True)
    assert adapter.spec("parecer", "R08").model == "synthetic-live-model"
    assert calls == []
    with run:
        with run.step("fake_live"):
            result = asyncio.run(adapter.annotate(make_request(), kind="parecer"))
            asyncio.run(adapter.annotate(make_request(), kind="parecer"))
    assert result.output is not None and len(calls) == 1
    assert all(call.model == "synthetic-live-model" for call in invocations)
    assert "scripted_scenarios" not in run.metadata


@pytest.mark.parametrize("live", [False, True])
def test_zero_budget_does_not_construct_any_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocations: list[Invocation],
    live: bool,
) -> None:
    def forbidden(*arguments: object, **keywords: object) -> AgentRuntime:
        pytest.fail("zero budget must not construct a runtime")

    monkeypatch.setattr(gateway, "build_codex_runtime", forbidden)
    monkeypatch.setattr(gateway, "build_scripted_runtime", forbidden)
    run = make_run(tmp_path)
    adapter = make_gateway(run, budget=0, live=live)
    adapter.spec("parecer", "R08")
    with run:
        with run.step("budget"):
            with pytest.raises(RuntimeError, match="positive per-run call budget"):
                asyncio.run(adapter.annotate(make_request(), kind="parecer"))
    assert invocations == [] and "scripted_scenarios" not in run.metadata


def test_failure_exposes_only_redacted_kind_code_and_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocations: list[Invocation]
) -> None:
    def failing_runtime(scenarios: Iterable[ScriptedAgentScenario]) -> AgentRuntime:
        return build_scripted_runtime(
            [
                ScriptedAgentScenario(
                    result=AgentExecutionResult.from_failure(
                        Failure(
                            kind=FailureKind.TRANSIENT_PROVIDER,
                            code="synthetic.failure",
                            message="provider failed token=synthetic-secret",
                            metadata={"private": "invoice-private-data"},
                        )
                    )
                )
            ]
        )

    monkeypatch.setattr(gateway, "build_scripted_runtime", failing_runtime)
    run = make_run(tmp_path)
    with run:
        with run.step("failure"):
            result = asyncio.run(
                make_gateway(run).annotate(make_request(), kind="parecer")
            )
    assert result.output is None and result.failure is not None
    assert set(result.failure) == {"kind", "code", "message"}
    assert result.failure["kind"] == "transient_provider"
    assert result.failure["code"] == "synthetic.failure"
    assert "synthetic-secret" not in json.dumps(result.failure)
    assert "invoice-private-data" not in json.dumps(result.failure)
    assert len(invocations) == 1


def test_successful_output_uses_same_private_trace_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invocations: list[Invocation]
) -> None:
    def secret_output(scenarios: Iterable[ScriptedAgentScenario]) -> AgentRuntime:
        return build_scripted_runtime(
            [
                ScriptedAgentScenario(
                    result=AgentExecutionResult.succeeded(
                        {
                            "fundamento": "Texto password=synthetic-secret preservado.",
                            "confianca": "baixa",
                            "evidencias_citadas": ["Contexto fornecido."],
                            "recomendacao": "revisar",
                        }
                    )
                )
            ]
        )

    monkeypatch.setattr(gateway, "build_scripted_runtime", secret_output)
    run = make_run(tmp_path)
    with run:
        with run.step("redacted_output"):
            result = asyncio.run(
                make_gateway(run).annotate(make_request(), kind="parecer")
            )
    assert result.output is not None and result.failure is None
    assert "synthetic-secret" not in json.dumps(result.output)
    assert "preservado" in str(result.output["fundamento"])
    assert len(invocations) == 1


@pytest.mark.parametrize("corruption", ["extra", "version", "enum", "duplicate"])
def test_scripted_fixture_requires_versioned_strict_annotation_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invocations: list[Invocation],
    corruption: str,
) -> None:
    original = default_llms_directory() / "scenarios/annotations-v1.json"
    document = json.loads(original.read_bytes())
    if corruption == "extra":
        document["unexpected"] = True
    elif corruption == "version":
        document["fixture_version"] = 1
    elif corruption == "enum":
        document["parecer"]["recomendacao"] = "aprovar"
    raw = json.dumps(document)
    if corruption == "duplicate":
        raw = raw.replace(
            '"fixture_version": "1"', '"fixture_version": "1", "fixture_version": "1"'
        )
    resources = tmp_path / "resources"
    (resources / "scenarios").mkdir(parents=True)
    (resources / "scenarios/annotations-v1.json").write_text(raw, encoding="utf-8")
    monkeypatch.setattr(gateway, "default_llms_directory", lambda: resources)
    run = make_run(tmp_path)
    with run:
        with run.step("invalid_fixture"):
            with pytest.raises(ValueError):
                asyncio.run(make_gateway(run).annotate(make_request(), kind="parecer"))
    assert invocations == [] and "scripted_scenarios" not in run.metadata


def test_gateway_requires_active_step_and_valid_rule_before_invocation(
    tmp_path: Path, invocations: list[Invocation]
) -> None:
    run = make_run(tmp_path)
    adapter = make_gateway(run)
    with pytest.raises(RuntimeError, match="not active"):
        asyncio.run(adapter.annotate(make_request(), kind="parecer"))
    with pytest.raises(ValueError, match="known rule ID"):
        adapter.spec("parecer", "R99")
    with run:
        with pytest.raises(RuntimeError, match="active run step"):
            asyncio.run(adapter.annotate(make_request(), kind="parecer"))
    assert invocations == []


def test_missing_deduction_is_null_in_both_versioned_agent_inputs(
    tmp_path: Path, invocations: list[Invocation]
) -> None:
    request = make_request(text="Texto da nota fiscal.")
    request = replace(request, record=replace(request.record, valor_glosa=None))
    run = make_run(tmp_path)
    adapter = make_gateway(run)
    with run:
        with run.step("nullable_deduction"):
            opinion = asyncio.run(adapter.annotate(request, kind="parecer"))
            invoice = asyncio.run(adapter.annotate(request, kind="nota_fiscal"))
    assert opinion.failure is None and invoice.failure is None
    assert len(invocations) == 2
    for call in invocations:
        assert call.prompt.version == (
            "3" if call.prompt.name == "explicar-achado" else "2"
        )
        assert isinstance(call.encoded_input, dict)
        record = call.encoded_input["registro"]
        assert isinstance(record, dict)
        assert record["valor_glosa"] is None
        assert record["valor_liquido"] == "90071992547409.91"

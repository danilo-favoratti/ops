"""Convert original audit context into traced, strictly typed agent annotations."""

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar, cast

from agentrig.agents import AgentExecutionResult, AgentRuntime
from agentrig.core import JsonValue as AgentJson
from agentrig.core import redact_private_trace_content
from duckdb import DuckDBPyConnection

from opsiu.adapters.agents.contracts import (
    NotaFiscalLeituraCodec,
    NotaFiscalRequestCodec,
    ParecerCodec,
    ParecerRequestCodec,
    StrictCodec,
    configure_nota_fiscal_agent,
    configure_parecer_agent,
)
from opsiu.adapters.agents.prompts import Prompt, default_llms_directory, load_prompt
from opsiu.adapters.agents.runtimes import (
    CODEX_CAPABILITIES,
    SCRIPTED_CAPABILITIES,
    ScriptedAgentScenario,
    build_codex_runtime,
    build_scripted_runtime,
)
from opsiu.adapters.agents.trace import TracedAgentInvoker, TracedAgentResult
from opsiu.application.ports import (
    AnnotationKind,
    AnnotationRequest,
    AnnotationResult,
    AnnotationSpec,
    FindingExportRecord,
    JsonValue,
)
from opsiu.application.runs import Run
from opsiu.domain.findings import evidence_json
from opsiu.settings import Settings

_TASKS: dict[AnnotationKind, str] = {
    "parecer": "explicar-achado",
    "nota_fiscal": "revisar-nota",
}
_FIXTURE_NAME = "annotations-v1.json"
OutputT = TypeVar("OutputT")


def _application_json(value: AgentJson) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _application_json(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_application_json(child) for child in value]
    return value


def _redacted_object(value: Mapping[str, AgentJson]) -> dict[str, JsonValue]:
    converted = _application_json(redact_private_trace_content(value))
    assert isinstance(converted, dict)
    return converted


def _expense(record: FindingExportRecord) -> dict[str, AgentJson]:
    return {
        "id_documento": record.finding.id_documento,
        "numero_deputado_id": record.numero_deputado_id,
        "nome_parlamentar": record.nome_parlamentar,
        "sigla_uf": record.sigla_uf,
        "sigla_partido": record.sigla_partido,
        "ano": record.ano,
        "mes": record.mes,
        "fornecedor": record.fornecedor,
        "cnpj_cpf": record.cnpj_cpf_digits,
        "numero": record.numero,
        "data_emissao": (
            record.data_emissao.isoformat() if record.data_emissao is not None else None
        ),
        "valor_documento": format(record.valor_documento, ".2f"),
        "valor_glosa": format(record.valor_glosa, ".2f"),
        "valor_liquido": format(record.valor_liquido, ".2f"),
        "url_documento": record.url_documento,
    }


def _pdf_text(request: AnnotationRequest) -> str | None:
    if request.pdf is None:
        return None
    content = request.pdf.content
    if (
        content.id_documento != request.record.finding.id_documento
        or content.url != request.record.url_documento
    ):
        raise ValueError("PDF context does not match the original expense document")
    return content.text if content.text.strip() and not content.needs_ocr else None


def _annotation_result(
    traced: TracedAgentResult[OutputT], codec: StrictCodec[OutputT]
) -> AnnotationResult:
    if traced.result.is_success:
        output = codec.encode(traced.result.unwrap())
        if not isinstance(output, Mapping):
            raise ValueError("agent annotation must encode a JSON object")
        return AnnotationResult(traced.trace_id, _redacted_object(output), None)
    failure = traced.result.failure
    assert failure is not None
    return AnnotationResult(
        traced.trace_id,
        None,
        _redacted_object(
            {
                "kind": failure.kind.value,
                "code": failure.code,
                "message": failure.message,
            }
        ),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate scripted fixture key: {key}")
        result[key] = value
    return result


class AgentRigGateway:
    def __init__(
        self,
        connection: DuckDBPyConnection,
        run: Run,
        settings: Settings,
        *,
        live: bool = False,
        repo_root: Path | None = None,
    ) -> None:
        if type(live) is not bool:
            raise ValueError("live must be an explicit boolean")
        self.connection = connection
        self.run = run
        self.settings = settings
        self.live = live
        self.repo_root = repo_root
        self.model = settings.agents.model if live else "scripted"
        self._prompts: dict[AnnotationKind, Prompt] = {}
        self._runtimes: dict[AnnotationKind, AgentRuntime] = {}
        self._fixtures: dict[AnnotationKind, AgentJson] | None = None

    def _prompt(self, kind: AnnotationKind) -> Prompt:
        if kind not in _TASKS:
            raise ValueError(f"unknown annotation kind: {kind!r}")
        if kind not in self._prompts:
            self._prompts[kind] = load_prompt(_TASKS[kind])
        return self._prompts[kind]

    def spec(self, kind: AnnotationKind, rule_id: str) -> AnnotationSpec:
        if re.fullmatch(r"R(?:0[1-9]|1[0-4])", rule_id) is None:
            raise ValueError("known rule ID required")
        prompt = self._prompt(kind)
        return AnnotationSpec(
            kind=kind,
            agent_id=f"explicar-achado-{rule_id}"
            if kind == "parecer"
            else "revisar-nota",
            model=self.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
        )

    def _load_fixtures(self) -> dict[AnnotationKind, AgentJson]:
        if self._fixtures is not None:
            return self._fixtures
        raw = (default_llms_directory() / "scenarios" / _FIXTURE_NAME).read_bytes()
        document: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object
        )
        if not isinstance(document, dict) or set(document) != {
            "fixture_version",
            "parecer",
            "nota_fiscal",
        }:
            raise ValueError(
                "scripted fixture must contain its version and both annotations"
            )
        if document["fixture_version"] != "1":
            raise ValueError("unsupported scripted fixture version")
        opinion_codec = ParecerCodec()
        invoice_codec = NotaFiscalLeituraCodec()
        fixtures: dict[AnnotationKind, AgentJson] = {
            "parecer": opinion_codec.encode(
                opinion_codec.decode(cast(AgentJson, document["parecer"]))
            ),
            "nota_fiscal": invoice_codec.encode(
                invoice_codec.decode(cast(AgentJson, document["nota_fiscal"]))
            ),
        }
        metadata: dict[str, JsonValue] = {
            "version": "1",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        scenarios = self.run.metadata.setdefault("scripted_scenarios", {})
        assert isinstance(scenarios, dict)
        previous = scenarios.get(_FIXTURE_NAME)
        if previous is not None and previous != metadata:
            raise ValueError("scripted fixture changed within the active run")
        scenarios[_FIXTURE_NAME] = metadata
        self._fixtures = fixtures
        return fixtures

    def _runtime(self, kind: AnnotationKind) -> AgentRuntime:
        if self.settings.agents.max_calls_per_run < 1:
            raise RuntimeError("agent runtime requires a positive per-run call budget")
        if kind not in self._runtimes:
            if self.live:
                runtime: AgentRuntime = build_codex_runtime(
                    self.settings, repo_root=self.repo_root
                )
            else:
                output = self._load_fixtures()[kind]
                runtime = build_scripted_runtime(
                    ScriptedAgentScenario(result=AgentExecutionResult.succeeded(output))
                    for _ in range(self.settings.agents.max_calls_per_run)
                )
            self._runtimes[kind] = runtime
        return self._runtimes[kind]

    async def annotate(
        self, request: AnnotationRequest, *, kind: AnnotationKind
    ) -> AnnotationResult:
        self.run.require_step()
        finding = request.record.finding
        self.spec(kind, finding.rule_id)
        prompt = self._prompt(kind)
        text = _pdf_text(request)
        expense = _expense(request.record)
        capabilities = CODEX_CAPABILITIES if self.live else SCRIPTED_CAPABILITIES
        invoker = TracedAgentInvoker(self.connection, self.run, model=self.model)
        if kind == "parecer":
            opinion_request = ParecerRequestCodec().decode(
                {
                    "achado": {
                        "finding_key": finding.finding_key,
                        "regra": finding.rule_id,
                        "versao_regra": finding.rule_version,
                        "severidade": finding.severity,
                        "valor_envolvido": format(finding.amount, ".2f"),
                        "evidencia_json": evidence_json(finding.evidence),
                        "source_sha256": finding.provenance.source.source_sha256,
                        "record_sha256": finding.provenance.source.record_sha256,
                    },
                    "registro": expense,
                    "texto_regra": request.rule_text,
                    "texto_nota": text,
                }
            )
            opinion_agent = configure_parecer_agent(
                self._runtime(kind),
                capabilities,
                rule_id=finding.rule_id,
                prompt=prompt,
            )
            opinion = await invoker.invoke(
                opinion_agent,
                opinion_request,
                prompt=prompt,
                finding_key=finding.finding_key,
            )
            return _annotation_result(opinion, ParecerCodec())
        if text is None:
            raise ValueError(
                "invoice review requires usable text from the original PDF"
            )
        invoice_request = NotaFiscalRequestCodec().decode(
            {"registro": expense, "texto_pdf": text}
        )
        invoice_agent = configure_nota_fiscal_agent(
            self._runtime(kind), capabilities, prompt=prompt
        )
        invoice = await invoker.invoke(
            invoice_agent,
            invoice_request,
            prompt=prompt,
            finding_key=finding.finding_key,
        )
        return _annotation_result(invoice, NotaFiscalLeituraCodec())

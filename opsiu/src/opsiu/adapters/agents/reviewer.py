"""Trace deputy summaries while enforcing exact finding identity and severity."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from agentrig.agents import AgentExecutionResult, AgentRuntime
from agentrig.core import JsonValue as AgentJson
from duckdb import DuckDBPyConnection

from opsiu.adapters.agents.contracts import (
    ResumoDeputado,
    ResumoDeputadoCodec,
    ResumoDeputadoRequest,
    ResumoDeputadoRequestCodec,
    configure_resumo_deputado_agent,
)
from opsiu.adapters.agents.gateway import _annotation_result, _unique_object
from opsiu.adapters.agents.prompts import Prompt, default_llms_directory, load_prompt
from opsiu.adapters.agents.runtimes import (
    CODEX_CAPABILITIES,
    SCRIPTED_CAPABILITIES,
    ScriptedAgentScenario,
    build_codex_runtime,
    build_scripted_runtime,
)
from opsiu.adapters.agents.trace import TracedAgentInvoker
from opsiu.application.consolidate import summary_agent_input
from opsiu.application.ports import (
    AnnotationResult,
    DeputyReviewInput,
    JsonValue,
    SummarySpec,
)
from opsiu.application.runs import Run
from opsiu.settings import Settings

_SEVERITY_ORDER = {"alta": 0, "media": 1, "revisar": 2}
_FIXTURE_NAME = "consolidation-v1.json"


class _BoundSummaryCodec:
    """Run identity and ordering checks inside the configured agent's decoding."""

    def __init__(self, request: ResumoDeputadoRequest) -> None:
        self.delegate = ResumoDeputadoCodec()
        self.schema_id = self.delegate.schema_id
        self.severities = {
            item.achado.finding_key: item.achado.severidade for item in request.achados
        }

    def decode(self, value: AgentJson) -> ResumoDeputado:
        output = self.delegate.decode(value)
        if {entry.finding_key for entry in output.ranking} != set(self.severities):
            raise ValueError("summary ranking must cover exactly the supplied findings")
        order = []
        for entry in output.ranking:
            if entry.severidade != self.severities[entry.finding_key]:
                raise ValueError(
                    "summary ranking must preserve deterministic severities"
                )
            order.append(_SEVERITY_ORDER[entry.severidade])
        if order != sorted(order):
            raise ValueError("summary ranking must order alta, media, then revisar")
        return output


def _request(request: DeputyReviewInput) -> ResumoDeputadoRequest:
    for item in request.findings:
        if item.numero_deputado_id != request.numero_deputado_id:
            raise ValueError("summary finding belongs to another deputy")
        if item.finding.status != "active":
            raise ValueError("summary input must contain active findings")
    return ResumoDeputadoRequestCodec().decode(
        cast(AgentJson, summary_agent_input(request))
    )


class DeputyReviewerGateway:
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
        self._prompt: Prompt | None = None
        self._live_runtime: AgentRuntime | None = None
        self._fixture: tuple[str, tuple[str, ...], str] | None = None

    def _load_prompt(self) -> Prompt:
        if self._prompt is None:
            self._prompt = load_prompt("consolidar-deputado")
        return self._prompt

    def spec(self) -> SummarySpec:
        prompt = self._load_prompt()
        return SummarySpec(self.model, prompt.name, prompt.version, prompt.sha256)

    def _load_fixture(self) -> tuple[str, tuple[str, ...], str]:
        if self._fixture is not None:
            return self._fixture
        raw = (default_llms_directory() / "scenarios" / _FIXTURE_NAME).read_bytes()
        document: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object
        )
        if not isinstance(document, dict) or set(document) != {
            "fixture_version",
            "resumo",
            "pontos_atencao",
            "justificativa",
        }:
            raise ValueError("invalid scripted consolidation fixture fields")
        if document["fixture_version"] != "1":
            raise ValueError("unsupported scripted consolidation fixture version")
        summary, reason = document["resumo"], document["justificativa"]
        attention = document["pontos_atencao"]
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(attention, list)
            or not attention
            or any(not isinstance(item, str) or not item.strip() for item in attention)
        ):
            raise ValueError("scripted consolidation fixture requires nonempty text")
        fixture = (summary, tuple(cast(list[str], attention)), reason)
        metadata: dict[str, JsonValue] = {
            "version": "1",
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        scenarios = self.run.metadata.setdefault("scripted_scenarios", {})
        assert isinstance(scenarios, dict)
        previous = scenarios.get(_FIXTURE_NAME)
        if previous is not None and previous != metadata:
            raise ValueError("scripted consolidation fixture changed within the run")
        scenarios[_FIXTURE_NAME] = metadata
        self._fixture = fixture
        return fixture

    def _runtime(self, request: ResumoDeputadoRequest) -> AgentRuntime:
        if self.settings.agents.max_calls_per_run < 1:
            raise RuntimeError("agent runtime requires a positive per-run call budget")
        if self.live:
            if self._live_runtime is None:
                self._live_runtime = build_codex_runtime(
                    self.settings, repo_root=self.repo_root
                )
            return self._live_runtime
        summary, attention, reason = self._load_fixture()
        ranking: list[AgentJson] = [
            {
                "finding_key": item.achado.finding_key,
                "severidade": item.achado.severidade,
                "justificativa": reason,
            }
            for item in sorted(
                request.achados,
                key=lambda item: (
                    _SEVERITY_ORDER[item.achado.severidade],
                    item.achado.finding_key,
                ),
            )
        ]
        output: AgentJson = {
            "resumo": summary,
            "ranking": ranking,
            "pontos_atencao": attention,
        }
        return build_scripted_runtime(
            [ScriptedAgentScenario(result=AgentExecutionResult.succeeded(output))]
        )

    async def summarize(self, request: DeputyReviewInput) -> AnnotationResult:
        self.run.require_step()
        typed_request = _request(request)
        prompt = self._load_prompt()
        configured = configure_resumo_deputado_agent(
            self._runtime(typed_request),
            CODEX_CAPABILITIES if self.live else SCRIPTED_CAPABILITIES,
            prompt=prompt,
        )
        agent = replace(configured, output_codec=_BoundSummaryCodec(typed_request))
        traced = await TracedAgentInvoker(
            self.connection, self.run, model=self.model
        ).invoke(agent, typed_request, prompt=prompt, finding_key=None)
        return _annotation_result(traced, ResumoDeputadoCodec())

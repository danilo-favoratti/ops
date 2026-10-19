"""Opt-in live Codex example: one typed decision agent, no tools.

Adapted from AgentRig's structured-agent example:
https://github.com/mtmoreira/agentrig/tree/main/examples/agents/codex_structured_agent

Run from the repository root:
  AGENTRIG_RUN_LIVE=1 .venv/bin/python3 experiment/codex_agent/example_codex_agent.py
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agentrig.agents import Agent, AgentContract, AgentLimits, AgentRuntime, ConfiguredAgent
from agentrig.core import (
    CancellationSource,
    Deadline,
    EffectProfile,
    EventId,
    InMemoryEventSink,
    JsonValue,
    RunContext,
    RunId,
    SystemClock,
    Uuid4IdGenerator,
)
from agentrig.integrations.openai import (
    CODEX_AGENT_RUNTIME_CAPABILITY,
    CodexAgentRuntime,
    CodexSandboxMode,
    CodexSandboxPolicy,
)
from agentrig.integrations.openai.sdk import CodexSdkClientFactory

REQUEST_SCHEMA = "example.decision-request.v1"
BRIEF_SCHEMA = "example.decision-brief.v1"
BRIEF_JSON_SCHEMA: Mapping[str, JsonValue] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "recommendation": {"type": "string", "enum": ["proceed", "revise", "stop"]},
    },
    "required": ["summary", "risks", "recommendation"],
    "additionalProperties": False,
}
INSTRUCTIONS = (
    "Analyze the encoded decision request without using tools. Return one "
    "concise JSON object matching the configured output schema."
)


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionRequest:
    question: str = field(repr=False)
    constraints: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionBrief:
    summary: str = field(repr=False)
    risks: tuple[str, ...] = field(repr=False)
    recommendation: str


@dataclass(frozen=True, slots=True)
class DecisionRequestCodec:
    schema_id: str = REQUEST_SCHEMA

    def encode(self, value: DecisionRequest) -> JsonValue:
        if not isinstance(value, DecisionRequest):
            raise ValueError("decision request required")
        question = value.question.strip()
        constraints = tuple(item.strip() for item in value.constraints)
        if not question:
            raise ValueError("decision question must not be empty")
        if not constraints or any(not item for item in constraints):
            raise ValueError("decision constraints must be nonempty")
        if len(constraints) != len(set(constraints)):
            raise ValueError("decision constraints must be unique")
        return {"question": question, "constraints": constraints}


@dataclass(frozen=True, slots=True)
class DecisionBriefCodec:
    schema_id: str = BRIEF_SCHEMA

    def decode(self, value: JsonValue) -> DecisionBrief:
        if not isinstance(value, Mapping) or set(value) != {
            "summary",
            "risks",
            "recommendation",
        }:
            raise ValueError("decision brief object required")
        summary = value["summary"]
        encoded_risks = value["risks"]
        recommendation = value["recommendation"]
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("decision summary required")
        if not isinstance(encoded_risks, (list, tuple)) or not encoded_risks:
            raise ValueError("at least one decision risk required")
        risks: list[str] = []
        for risk in encoded_risks:
            if not isinstance(risk, str) or not risk.strip():
                raise ValueError("decision risks must be nonempty strings")
            risks.append(risk.strip())
        if len(risks) != len(set(risks)):
            raise ValueError("decision risks must be unique")
        if recommendation not in {"proceed", "revise", "stop"}:
            raise ValueError("decision recommendation is invalid")
        return DecisionBrief(
            summary=summary.strip(),
            risks=tuple(risks),
            recommendation=recommendation,
        )


def configure_agent(runtime: AgentRuntime) -> Agent[DecisionRequest, DecisionBrief]:
    return ConfiguredAgent(
        runtime=runtime,
        contract=AgentContract(
            agent_id="decision-brief",
            version="1",
            purpose="Produce one bounded structured decision brief",
            input_schema=REQUEST_SCHEMA,
            output_schema=BRIEF_SCHEMA,
            prompt_version="decision-prompt-1",
            effect_profile=EffectProfile.READ_ONLY,
            limits=AgentLimits(max_turns=1, max_tool_calls=0),
            stopping_policy="output_schema_satisfied",
            allowed_capabilities=(CODEX_AGENT_RUNTIME_CAPABILITY.capability_id,),
            permissions={"workspace": "read_only", "network": "denied"},
        ),
        instructions=INSTRUCTIONS,
        input_codec=DecisionRequestCodec(),
        output_codec=DecisionBriefCodec(),
    )


def _model() -> str:
    model = os.environ.get("AGENTRIG_CODEX_LIVE_MODEL", "gpt-5.6-terra")
    if not model or model != model.strip():
        raise ValueError("AGENTRIG_CODEX_LIVE_MODEL must be nonempty and trimmed")
    return model


async def run_live() -> dict[str, object]:
    if os.environ.get("AGENTRIG_RUN_LIVE") != "1":
        raise RuntimeError("live Codex example requires AGENTRIG_RUN_LIVE=1")

    model = _model()
    runtime = CodexAgentRuntime(
        client_factory=CodexSdkClientFactory(),
        model=model,
        sandbox=CodexSandboxPolicy(
            mode=CodexSandboxMode.READ_ONLY,
            cwd=str(Path.cwd().resolve()),
            network_access=False,
        ),
        output_schemas={BRIEF_SCHEMA: BRIEF_JSON_SCHEMA},
        ephemeral=True,
    )
    clock = SystemClock()
    events = InMemoryEventSink()
    context = RunContext.create_root(
        clock=clock,
        id_generator=Uuid4IdGenerator(RunId),
        cancellation=CancellationSource().token,
        event_sink=events,
        event_id_generator=Uuid4IdGenerator(EventId),
        deadline=Deadline.after(120.0, clock),
        labels={"example_mode": "live"},
    )
    result = await configure_agent(runtime).run(
        DecisionRequest(
            question="Should this release check stay deterministic offline?",
            constraints=(
                "The default path must not call a provider.",
                "A Codex run must be an explicit opt-in.",
            ),
        ),
        context,
    )
    if not result.is_success:
        failure = result.failure
        if failure is None:
            raise RuntimeError("Codex agent failed without a failure")
        exception_type = failure.metadata.get("exception_type")
        detail = (
            f" exception_type={exception_type}"
            if isinstance(exception_type, str)
            else ""
        )
        raise RuntimeError(
            "Codex agent failed with normalized "
            f"kind={failure.kind.value} code={failure.code or 'none'}{detail}"
        )
    brief = result.unwrap()
    return {
        "event_kinds": [event.kind.value for event in events.events],
        "model": model,
        "recommendation": brief.recommendation,
        "risk_count": len(brief.risks),
        "runtime": "openai.codex",
        "summary": brief.summary,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run_live()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

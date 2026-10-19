"""Explicitly gated Codex configuration and deterministic scripted runtimes."""

import os
from collections.abc import Iterable
from pathlib import Path

from agentrig.integrations.openai import (
    CODEX_AGENT_RUNTIME_CAPABILITY,
    CodexAgentRuntime,
    CodexApprovalMode,
    CodexClient,
    CodexClientFactory,
    CodexSandboxMode,
    CodexSandboxPolicy,
)
from agentrig.integrations.openai.sdk import CodexSdkClientFactory
from agentrig.testing import ScriptedAgentRuntime
from agentrig.testing import ScriptedAgentScenario as ScriptedAgentScenario

from opsiu.adapters.agents.contracts import OUTPUT_SCHEMAS
from opsiu.settings import Settings

CODEX_CAPABILITIES = (CODEX_AGENT_RUNTIME_CAPABILITY.capability_id,)
SCRIPTED_CAPABILITIES = ("opsiu.scripted",)


def _require_live(gate: str) -> None:
    if os.environ.get("AGENTRIG_RUN_LIVE") != "1":
        raise RuntimeError("live Codex runtime requires AGENTRIG_RUN_LIVE=1")
    if os.environ.get(gate) != "1":
        raise RuntimeError(f"live Codex runtime additionally requires {gate}=1")


class _LiveGatedClientFactory:
    """Recheck opt-in for every execution immediately before SDK client creation."""

    def __init__(self, factory: CodexClientFactory, gate: str) -> None:
        self.factory = factory
        self.gate = gate

    def create(self) -> CodexClient:
        _require_live(self.gate)
        return self.factory.create()


def _repository_root(explicit: Path | None) -> Path:
    if explicit is not None:
        root = explicit.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError(root)
        return root
    ancestors = Path(__file__).resolve().parents
    for ancestor in ancestors:
        if (ancestor / ".git").exists():
            return ancestor
    for ancestor in ancestors:
        if (ancestor / "pyproject.toml").is_file() and (
            ancestor / "src/opsiu"
        ).is_dir():
            return ancestor
    current = Path.cwd().resolve()
    for ancestor in (current, *current.parents):
        if (ancestor / ".git").exists():
            return ancestor
    return current


def build_codex_runtime(
    settings: Settings, *, repo_root: Path | None = None
) -> CodexAgentRuntime:
    """Configure a read-only, network-denied, ephemeral runtime without calling it.

    The literal AGENTRIG_RUN_LIVE=1 opt-in is mandatory at construction and before
    each SDK client creation; a configured custom gate is additional. An explicit
    root must be an existing directory. Defaults use
    this module's Git checkout or exported OPSIU source project. An installed
    package uses the caller's Git root, or the caller's current directory when
    outside Git. No model call, client process, or budget consumption occurs here.
    """
    gate = settings.agents.live_env_var
    _require_live(gate)
    root = _repository_root(repo_root)
    return CodexAgentRuntime(
        client_factory=_LiveGatedClientFactory(CodexSdkClientFactory(), gate),
        model=settings.agents.model,
        sandbox=CodexSandboxPolicy(
            mode=CodexSandboxMode.READ_ONLY,
            cwd=str(root),
            network_access=False,
        ),
        output_schemas=OUTPUT_SCHEMAS,
        approval_mode=CodexApprovalMode.DENY_ALL,
        ephemeral=True,
    )


def build_scripted_runtime(
    scenarios: Iterable[ScriptedAgentScenario],
) -> ScriptedAgentRuntime:
    """Consume each supplied offline scenario once, then report exhaustion."""
    return ScriptedAgentRuntime(scenarios=scenarios, repeat_last=False)

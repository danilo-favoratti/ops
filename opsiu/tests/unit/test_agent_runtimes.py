"""Runtime construction preserves the live gate and never invokes a provider."""

import asyncio
import os
from pathlib import Path
from typing import cast

import pytest
from agentrig.agents import AgentExecutionRequest, AgentExecutionResult
from agentrig.core import (
    CancellationSource,
    EventId,
    InMemoryEventSink,
    RunContext,
    RunId,
    SystemClock,
    Uuid4IdGenerator,
)
from agentrig.integrations.openai import (
    CODEX_AGENT_RUNTIME_CAPABILITY,
    CodexAgentRuntime,
    CodexApprovalMode,
    CodexClient,
    CodexClientFactory,
    CodexSandboxMode,
    CodexSandboxPolicy,
)
from agentrig.testing import ScriptedAgentScenario as SdkScenario

from opsiu.adapters.agents import runtimes
from opsiu.adapters.agents.contracts import OUTPUT_SCHEMAS, configure_parecer_agent
from opsiu.adapters.agents.prompts import Prompt
from opsiu.adapters.agents.runtimes import (
    CODEX_CAPABILITIES,
    SCRIPTED_CAPABILITIES,
    ScriptedAgentScenario,
    build_codex_runtime,
    build_scripted_runtime,
)
from opsiu.settings import AgentSettings, Settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def runtime_config(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    class Factory:
        def create(self) -> CodexClient:
            pytest.fail("configuration tests must not create an SDK client")

    factory = Factory()
    runtime = object()

    def capture(**arguments: object) -> CodexAgentRuntime:
        assert isinstance(arguments["client_factory"], CodexClientFactory)
        assert arguments["client_factory"] is not factory
        captured.update(arguments)
        return cast(CodexAgentRuntime, runtime)

    monkeypatch.setattr(runtimes, "CodexSdkClientFactory", lambda: factory)
    monkeypatch.setattr(runtimes, "CodexAgentRuntime", capture)
    return captured


@pytest.mark.parametrize("gate", [None, "", "0", "true", "yes", " 1", "1 "])
def test_live_factory_requires_literal_gate_before_constructing_client(
    monkeypatch: pytest.MonkeyPatch, gate: str | None
) -> None:
    def forbidden_factory() -> None:
        pytest.fail("SDK factory must not be constructed without the live gate")

    monkeypatch.setattr(runtimes, "CodexSdkClientFactory", forbidden_factory)
    if gate is not None:
        monkeypatch.setenv("AGENTRIG_RUN_LIVE", gate)
    with pytest.raises(RuntimeError, match="AGENTRIG_RUN_LIVE=1"):
        build_codex_runtime(Settings())


def test_custom_gate_is_additional_and_cannot_bypass_literal_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_config: dict[str, object],
) -> None:
    settings = Settings(agents=AgentSettings(live_env_var="OPSIU_CUSTOM_LIVE"))
    monkeypatch.setenv("OPSIU_CUSTOM_LIVE", "1")
    with pytest.raises(RuntimeError, match="AGENTRIG_RUN_LIVE=1"):
        build_codex_runtime(settings, repo_root=tmp_path)
    monkeypatch.delenv("OPSIU_CUSTOM_LIVE")
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    with pytest.raises(RuntimeError, match="OPSIU_CUSTOM_LIVE=1"):
        build_codex_runtime(settings, repo_root=tmp_path)
    assert runtime_config == {}
    monkeypatch.setenv("OPSIU_CUSTOM_LIVE", "1")
    build_codex_runtime(settings, repo_root=tmp_path)
    assert runtime_config


@pytest.mark.parametrize("removed_gate", ["AGENTRIG_RUN_LIVE", "OPSIU_CUSTOM_LIVE"])
def test_already_built_runtime_rechecks_both_gates_before_sdk_client_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removed_gate: str
) -> None:
    calls: list[str] = []

    class Factory:
        def create(self) -> CodexClient:
            calls.append("create")
            raise AssertionError("SDK client creation must remain gated")

    monkeypatch.setattr(runtimes, "CodexSdkClientFactory", Factory)
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    monkeypatch.setenv("OPSIU_CUSTOM_LIVE", "1")
    runtime = build_codex_runtime(
        Settings(agents=AgentSettings(live_env_var="OPSIU_CUSTOM_LIVE")),
        repo_root=tmp_path,
    )
    agent = configure_parecer_agent(
        runtime,
        CODEX_CAPABILITIES,
        rule_id="R08",
        prompt=Prompt("explicar-achado", "Explique o achado.", "1", "a" * 64),
    )
    request = AgentExecutionRequest(
        contract=agent.contract, instructions="Teste local.", input={}
    )
    context = RunContext.create_root(
        clock=SystemClock(),
        id_generator=Uuid4IdGenerator(RunId),
        cancellation=CancellationSource().token,
        event_sink=InMemoryEventSink(),
        event_id_generator=Uuid4IdGenerator(EventId),
    )
    monkeypatch.delenv(removed_gate)
    result = asyncio.run(runtime.execute(request, context))
    assert result.result.failure is not None
    assert calls == []


def test_codex_configuration_is_read_only_ephemeral_network_denied_with_exact_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_config: dict[str, object],
) -> None:
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    repository = tmp_path / "repository"
    repository.mkdir()
    settings = Settings(
        agents=AgentSettings(model="synthetic-model", max_calls_per_run=0)
    )
    build_codex_runtime(settings, repo_root=Path("repository"))
    sandbox = runtime_config["sandbox"]
    assert isinstance(sandbox, CodexSandboxPolicy)
    assert sandbox.mode is CodexSandboxMode.READ_ONLY
    assert sandbox.cwd == str(repository)
    assert sandbox.writable_roots == ()
    assert sandbox.network_access is False
    assert runtime_config["ephemeral"] is True
    assert runtime_config["approval_mode"] is CodexApprovalMode.DENY_ALL
    assert runtime_config["model"] == "synthetic-model"
    assert runtime_config["output_schemas"] is OUTPUT_SCHEMAS
    assert CODEX_CAPABILITIES == (CODEX_AGENT_RUNTIME_CAPABILITY.capability_id,)
    assert SCRIPTED_CAPABILITIES == ("opsiu.scripted",)


@pytest.mark.parametrize("exists", [False, True])
def test_explicit_sandbox_root_must_be_an_existing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_config: dict[str, object],
    exists: bool,
) -> None:
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    path = tmp_path / "invalid"
    if exists:
        path.touch()
    error = NotADirectoryError if exists else FileNotFoundError
    with pytest.raises(error):
        build_codex_runtime(Settings(), repo_root=path)
    assert runtime_config == {}


def fake_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    checkout = tmp_path / "checkout"
    path = checkout / "opsiu/src/opsiu/adapters/agents/runtimes.py"
    path.parent.mkdir(parents=True)
    path.touch()
    monkeypatch.setattr(runtimes, "__file__", str(path))
    return checkout


@pytest.mark.parametrize("worktree", [False, True])
def test_default_root_is_module_git_checkout_independent_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_config: dict[str, object],
    worktree: bool,
) -> None:
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    checkout = fake_module(tmp_path, monkeypatch)
    marker = checkout / ".git"
    marker.touch() if worktree else marker.mkdir()
    (checkout / "opsiu/pyproject.toml").touch()
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / ".git").mkdir()
    monkeypatch.chdir(caller)
    build_codex_runtime(Settings())
    sandbox = runtime_config["sandbox"]
    assert isinstance(sandbox, CodexSandboxPolicy)
    assert sandbox.cwd == str(checkout)


def test_exported_source_project_is_used_without_git_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_config: dict[str, object],
) -> None:
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    checkout = fake_module(tmp_path, monkeypatch)
    project = checkout / "opsiu"
    (project / "pyproject.toml").touch()
    build_codex_runtime(Settings())
    sandbox = runtime_config["sandbox"]
    assert isinstance(sandbox, CodexSandboxPolicy)
    assert sandbox.cwd == str(project)


@pytest.mark.parametrize("git_repository", [False, True])
def test_installed_package_uses_callers_git_root_or_current_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_config: dict[str, object],
    git_repository: bool,
) -> None:
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    installed = tmp_path / "site-packages/opsiu/adapters/agents/runtimes.py"
    installed.parent.mkdir(parents=True)
    installed.touch()
    monkeypatch.setattr(runtimes, "__file__", str(installed))
    caller = tmp_path / "caller"
    nested = caller / "nested"
    nested.mkdir(parents=True)
    if git_repository:
        (caller / ".git").mkdir()
    monkeypatch.chdir(nested)
    build_codex_runtime(Settings())
    sandbox = runtime_config["sandbox"]
    assert isinstance(sandbox, CodexSandboxPolicy)
    assert sandbox.cwd == str(caller if git_repository else nested)


def test_scripted_runtime_consumes_iterable_once_and_never_repeats_last_scenario() -> (
    None
):
    assert ScriptedAgentScenario is SdkScenario
    scenarios = (
        ScriptedAgentScenario(result=AgentExecutionResult.succeeded(value))
        for value in ("first", "second")
    )
    runtime = build_scripted_runtime(scenarios)
    agent = configure_parecer_agent(
        runtime,
        SCRIPTED_CAPABILITIES,
        rule_id="R08",
        prompt=Prompt("explicar-achado", "Explique o achado.", "1", "a" * 64),
    )
    request = AgentExecutionRequest(
        contract=agent.contract, instructions="Teste local.", input={}
    )
    context = RunContext.create_root(
        clock=SystemClock(),
        id_generator=Uuid4IdGenerator(RunId),
        cancellation=CancellationSource().token,
        event_sink=InMemoryEventSink(),
        event_id_generator=Uuid4IdGenerator(EventId),
    )

    async def execute() -> tuple[AgentExecutionResult, ...]:
        first = await runtime.execute(request, context)
        assert not runtime.is_exhausted
        second = await runtime.execute(request, context)
        assert runtime.is_exhausted
        exhausted = await runtime.execute(request, context)
        return first, second, exhausted

    first, second, exhausted = asyncio.run(execute())
    assert first.result.output == "first"
    assert second.result.output == "second"
    assert exhausted.result.failure is not None
    assert exhausted.result.failure.code == "scripted_agent_runtime.exhausted"
    assert [call.index for call in runtime.calls] == [0, 1, 2]


def test_scripted_runtime_requires_at_least_one_scenario() -> None:
    with pytest.raises(ValueError, match="at least one scenario"):
        build_scripted_runtime(())

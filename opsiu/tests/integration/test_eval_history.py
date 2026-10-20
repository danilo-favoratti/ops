"""Evaluations keep independent run histories, budgets, and private model inputs."""

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path

import pytest
from agentrig.agents import AgentExecutionResult, AgentRuntime

from evals.dataset import CASES_PATH, LOCAL_RULE_IDS, load_cases
from evals.runner import run_evaluation
from opsiu.adapters.agents import gateway
from opsiu.adapters.agents.runtimes import (
    ScriptedAgentScenario,
    build_scripted_runtime,
)
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.settings import AgentSettings, PathSettings, SamplingSettings, Settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)

    def forbidden_live(*arguments: object, **keywords: object) -> AgentRuntime:
        pytest.fail("ordinary integration tests must never build a live runtime")

    monkeypatch.setattr(gateway, "build_codex_runtime", forbidden_live)


def settings(tmp_path: Path, *, budget: int = 20, per_rule: int = 20) -> Settings:
    return Settings(
        paths=PathSettings(
            data_dir=tmp_path / "production-input",
            db_path=tmp_path / "production.duckdb",
            runs_dir=tmp_path / "runs",
            external_dir=tmp_path / "production-external",
        ),
        agents=AgentSettings(max_calls_per_run=budget),
        sampling=SamplingSettings(per_rule=per_rule),
    )


def test_eval_budget_and_coverage_have_real_trace_lineage_without_label_leakage(
    tmp_path: Path,
) -> None:
    result = run_evaluation(settings(tmp_path))
    assert result.dataset_size == result.selected == 50
    assert result.attempted == result.succeeded == 20
    assert result.failed == 0
    assert result.coverage == pytest.approx(20 / 50)
    assert result.agreement == pytest.approx(result.matches / 20)
    assert result.status == "completed"
    assert not (tmp_path / "production.duckdb").exists()
    assert not (tmp_path / "production-input").exists()
    assert not (tmp_path / "production-external").exists()
    with connect_database(result.run_dir / "eval.duckdb", read_only=True) as db:
        traces = db.execute(
            "SELECT t.finding_key, f.rule_id, t.model, t.input_json, "
            "t.status, CAST(t.run_id AS VARCHAR), s.status "
            "FROM agent_traces t JOIN findings f USING (finding_key) "
            "JOIN steps s ON s.step_id=t.step_id"
        ).fetchall()
        assert db.execute("SELECT count(*) FROM runs").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM despesas").fetchone() == (800,)
        assert db.execute("SELECT count(*) FROM finding_records").fetchone() == (52,)
    assert len(traces) == 20
    assert {trace[1] for trace in traces} == set(LOCAL_RULE_IDS)
    assert {trace[2] for trace in traces} == {"scripted"}
    assert {trace[4] for trace in traces} == {"succeeded"}
    assert {trace[5] for trace in traces} == {result.run_id}
    assert {trace[6] for trace in traces} == {"completed"}
    labels = {case.finding_key: case for case in load_cases()}
    assert result.matches == sum(
        labels[trace[0]].expected_recommendation == "revisar" for trace in traces
    )
    for trace in traces:
        assert "expected_recommendation" not in trace[3]
        assert "Hipótese sintética sem validação humana" not in trace[3]
        assert labels[trace[0]].notes not in trace[3]
    score = json.loads((result.run_dir / "eval_results.json").read_text())
    assert (
        score["dataset_sha256"] == hashlib.sha256(CASES_PATH.read_bytes()).hexdigest()
    )
    manifest = json.loads((result.run_dir / "manifest.json").read_text())
    assert manifest["counters"]["agent_calls"] == 20
    for artifact in manifest["artifacts"]:
        path = Path(artifact["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    assert (result.run_dir / "report.md").is_file()
    assert (result.run_dir / "eval.md").is_file()


def test_zero_budget_has_no_runtime_or_fabricated_perfect_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*arguments: object, **keywords: object) -> AgentRuntime:
        pytest.fail("zero budget must not construct a runtime")

    monkeypatch.setattr(gateway, "build_scripted_runtime", forbidden)
    result = run_evaluation(settings(tmp_path, budget=0))
    assert result.attempted == result.succeeded == result.failed == result.matches == 0
    assert result.agreement is None
    assert result.coverage == 0
    assert result.status == "completed"
    with connect_database(result.run_dir / "eval.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (0,)


def test_invalid_typed_output_consumes_budget_and_counts_as_failed_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invalid_runtime(scenarios: Iterable[ScriptedAgentScenario]) -> AgentRuntime:
        return build_scripted_runtime(
            [
                ScriptedAgentScenario(
                    result=AgentExecutionResult.succeeded(
                        {"recomendacao": "manter", "unexpected": "invalid contract"}
                    )
                )
            ]
        )

    monkeypatch.setattr(gateway, "build_scripted_runtime", invalid_runtime)
    result = run_evaluation(settings(tmp_path, budget=1))
    assert result.attempted == result.failed == 1
    assert result.succeeded == result.matches == 0
    assert result.agreement == 0
    assert result.coverage == 0
    assert result.status == "completed_with_errors"
    with connect_database(result.run_dir / "eval.duckdb", read_only=True) as db:
        assert db.execute("SELECT status FROM agent_traces").fetchall() == [("failed",)]
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (0,)
    artifact = json.loads((result.run_dir / "eval_results.json").read_text())
    assert len(artifact["cases"]) == 1
    assert artifact["cases"][0]["actual_recommendation"] is None
    assert artifact["cases"][0]["score"] == 0
    assert artifact["cases"][0]["trace_id"]


def test_sampling_and_repeat_preserve_prior_run_files(tmp_path: Path) -> None:
    configuration = settings(tmp_path, per_rule=1)
    first = run_evaluation(configuration)
    assert first.selected == first.attempted == first.succeeded == 9
    assert first.coverage == pytest.approx(9 / 50)
    hashes = {
        path.relative_to(first.run_dir): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in first.run_dir.rglob("*")
        if path.is_file()
    }
    second = run_evaluation(configuration)
    assert second.run_id != first.run_id and second.run_dir != first.run_dir
    assert second.matches == first.matches
    assert second.agreement == first.agreement
    assert second.coverage == first.coverage
    assert all(
        hashlib.sha256((first.run_dir / name).read_bytes()).hexdigest() == digest
        for name, digest in hashes.items()
    )

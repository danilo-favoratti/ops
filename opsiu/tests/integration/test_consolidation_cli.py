"""Deputy summaries preserve exact source opinions and deterministic findings."""

import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

import pytest
from agentrig.agents import AgentExecutionResult
from agentrig.testing import ScriptedAgentRuntime, ScriptedAgentScenario
from typer.testing import CliRunner, Result

from opsiu.adapters.agents import reviewer
from opsiu.adapters.agents.runtimes import build_scripted_runtime
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def invoke(*arguments: str, expected: int = 0) -> Result:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.exception or result.output
    return result


def run_id(result: Result) -> str:
    return result.stdout.splitlines()[0].removeprefix("run_id: ")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    shutil.copyfile(FIXTURE, tmp_path / "input/Ano-2099.json")
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        "[years]\nfrom=2099\nto=2099\n[agents]\nmax_calls_per_run=20\n",
        encoding="utf-8",
    )
    invoke("ingest")
    invoke("check", "--regras", "R01,R02,R08,R09,R10")
    invoke("explain")
    with connect_database(tmp_path / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (6,)
    return tmp_path


def test_summary_retry_force_and_new_opinion_preserve_history(workspace: Path) -> None:
    first = invoke("explain", "--consolidar", "--deputado", "101")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        summaries = db.execute(
            "SELECT summary_id, input_sha256, input_json, output_json, run_id, model "
            "FROM resumos_deputado WHERE numero_deputado_id=101"
        ).fetchall()
        assert len(summaries) == 1
        original = summaries[0]
        assert str(original[4]) == run_id(first)
        assert original[5] == "scripted"
        expected = dict(
            db.execute(
                "SELECT finding_key, severity FROM findings "
                "WHERE rule_id IN ('R01', 'R02')"
            ).fetchall()
        )
        output = json.loads(original[3])
        assert {
            item["finding_key"]: item["severidade"] for item in output["ranking"]
        } == (expected)
        assert "simulad" in output["resumo"].lower()
        assert all(key in original[2] for key in expected)
        assert db.execute("SELECT count(*) FROM resumo_fontes").fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (7,)
    invoke("explain", "--consolidar", "--deputado", "101")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM resumos_deputado").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (7,)
    invoke("explain", "--consolidar", "--deputado", "101", "--force")
    invoke("explain", "--regras", "R01", "--force")
    invoke("explain", "--consolidar", "--deputado", "101")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM resumos_deputado").fetchone() == (3,)
        assert db.execute(
            "SELECT count(DISTINCT input_sha256) FROM resumos_deputado"
        ).fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM resumo_fontes").fetchone() == (6,)
        assert (
            db.execute(
                "SELECT summary_id, input_sha256, input_json, output_json, "
                "run_id, model "
                "FROM resumos_deputado WHERE summary_id=?",
                [str(original[0])],
            ).fetchone()
            == original
        )
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (7,)
        assert db.execute(
            "SELECT count(*) FROM findings WHERE status='active'"
        ).fetchone() == (6,)


def test_summary_budget_resumes_other_deputies_without_repeating(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPSIU_AGENTS__MAX_CALLS_PER_RUN", "1")
    first = invoke("explain", "--consolidar")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM resumos_deputado").fetchone() == (1,)
        assert db.execute(
            "SELECT count(*) FROM events WHERE run_id=? AND kind='budget.exhausted'",
            [run_id(first)],
        ).fetchone() == (1,)
    invoke("explain", "--consolidar")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT count(*), count(DISTINCT numero_deputado_id) FROM resumos_deputado"
        ).fetchone() == (2, 2)
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (6,)
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (8,)


def test_foreign_summary_key_fails_trace_without_publishing_summary(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invalid_runtime(
        scenarios: Iterable[ScriptedAgentScenario],
    ) -> ScriptedAgentRuntime:
        del scenarios
        return build_scripted_runtime(
            [
                ScriptedAgentScenario(
                    result=AgentExecutionResult.succeeded(
                        {
                            "resumo": "Resumo sintético inválido para teste.",
                            "ranking": [
                                {
                                    "finding_key": "f" * 64,
                                    "severidade": "alta",
                                    "justificativa": "Chave externa de teste.",
                                }
                            ],
                            "pontos_atencao": [],
                        }
                    )
                )
            ]
        )

    monkeypatch.setattr(reviewer, "build_scripted_runtime", invalid_runtime)
    failed = invoke("explain", "--consolidar", "--deputado", "101", expected=1)
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM resumos_deputado").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM resumo_fontes").fetchone() == (0,)
        row = db.execute(
            "SELECT status, finding_key, output_json FROM agent_traces WHERE run_id=?",
            [run_id(failed)],
        ).fetchone()
        assert row is not None and row[:2] == ("failed", None)
        assert json.loads(row[2])["ranking"][0]["finding_key"] == "f" * 64
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (6,)


def test_live_summary_does_not_consume_scripted_opinions(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_runtime(*args: object, **kwargs: object) -> None:
        pytest.fail("no provider call is needed without opinions for that model")

    monkeypatch.setattr(reviewer, "build_codex_runtime", forbidden_runtime)
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    invoke("explain", "--consolidar", "--deputado", "101")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM resumos_deputado").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (6,)


def test_superseded_findings_leave_old_summary_intact(workspace: Path) -> None:
    invoke("explain", "--consolidar", "--deputado", "101")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        original = db.execute("SELECT * FROM resumos_deputado").fetchall()
    (workspace / "input/Ano-2099.json").write_text('{"dados": []}')
    invoke("ingest")
    invoke("check", "--regras", "R01,R02")
    invoke("explain", "--consolidar", "--deputado", "101")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT * FROM resumos_deputado").fetchall() == original
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (7,)
        assert db.execute("SELECT count(*) FROM resumo_fontes").fetchone() == (2,)


@pytest.mark.parametrize(
    "arguments",
    [
        ("--deputado", "101"),
        ("--consolidar", "--amostra", "1"),
        ("--consolidar", "--regras", "R01"),
    ],
)
def test_conflicting_summary_arguments_are_aborted_runs(
    workspace: Path, arguments: tuple[str, ...]
) -> None:
    invalid = invoke("explain", *arguments, expected=2)
    manifest = json.loads(
        (workspace / "runs" / run_id(invalid) / "manifest.json").read_text()
    )
    assert manifest["status"] == "aborted"

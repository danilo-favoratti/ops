"""Every real command leaves a readable report, including file-only failures."""

import json
import os
from pathlib import Path
from typing import NoReturn

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app, runtime
from opsiu.interfaces.web.app import create_app
from opsiu.settings import Settings

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        "[years]\nfrom=2099\nto=2099\n",
        encoding="utf-8",
    )
    return tmp_path


def invoke(
    workspace: Path, *arguments: str, expected: int = 0
) -> tuple[Path, str, str]:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.output
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    directory = workspace / "runs" / run_id
    report = directory / "report.md"
    assert report.is_file(), result.output
    assert result.stdout.splitlines()[-1].endswith(f"{run_id}/report.md")
    text = report.read_text(encoding="utf-8").replace("\\_", "_")
    assert run_id in text
    assert arguments[0] in text
    assert "Duração" in text
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["finished_at"] is not None
    return directory, text, result.stdout


def test_successful_file_only_and_database_runs_report_artifacts_and_show_links(
    workspace: Path,
) -> None:
    first, status_report, _ = invoke(workspace, "db", "status")
    assert not (workspace / "db.duckdb").exists()
    assert "status" in status_report
    migrate, migration_report, _ = invoke(workspace, "db", "migrate")
    assert "migrations_applied" in migration_report
    exported, final_report, _ = invoke(workspace, "report", "final")
    assert "final.csv" in final_report and "final.md" in final_report
    assert "findings_exported" in final_report
    previous = (exported / "report.md").read_bytes()
    shown, _, output = invoke(workspace, "runs", "show", exported.name, "--events")
    assert f"{exported.name}/report.md" in output
    assert (exported / "report.md").read_bytes() == previous
    assert len({first.name, migrate.name, exported.name, shown.name}) == 4
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone() == (3,)


def test_failed_command_without_database_has_error_message_in_report(
    workspace: Path,
) -> None:
    directory, report, _ = invoke(workspace, "report", "final", expected=1)
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "completed_with_errors"
    assert "LookupError" in report
    assert "Banco" in report
    assert "Falhas" in report
    assert not (workspace / "db.duckdb").exists()


def test_configuration_abort_before_steps_still_has_report(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPSIU_SAMPLING__PER_RULE", "0")
    directory, report, _ = invoke(workspace, "db", "migrate", expected=2)
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert manifest["steps"] == []
    assert "ConfigurationError" in report
    assert "per_rule" in report
    assert not (workspace / "db.duckdb").exists()


def test_database_open_failure_does_not_prevent_report(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_open(path: Path, *, read_only: bool = False) -> NoReturn:
        del path, read_only
        raise OSError("Banco sintético indisponível.")

    monkeypatch.setattr(runtime, "connect_database", fail_open)
    directory, report, _ = invoke(workspace, "db", "migrate", expected=1)
    assert "OSError" in report
    assert "Banco sintético indisponível" in report
    assert json.loads((directory / "manifest.json").read_text())["status"] == (
        "completed_with_errors"
    )


def test_agent_run_report_does_not_copy_private_invoice_or_prompt_payload(
    workspace: Path,
) -> None:
    data = json.loads(FIXTURE.read_text())
    record = data["dados"][39]
    record["fornecedor"] = "CONTEUDO_PRIVADO_DO_FORNECEDOR_2099"
    (workspace / "input").mkdir()
    (workspace / "input/Ano-2099.json").write_text(
        json.dumps({"dados": [record]}), encoding="utf-8"
    )
    invoke(workspace, "ingest")
    invoke(workspace, "check", "--regras", "R08")
    directory, report, _ = invoke(workspace, "explain", "--regras", "R08")
    assert "CONTEUDO_PRIVADO_DO_FORNECEDOR_2099" not in report
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        trace = db.execute(
            "SELECT input_json, instructions FROM agent_traces WHERE run_id=?",
            [directory.name],
        ).fetchone()
        assert trace is not None
        assert "CONTEUDO_PRIVADO_DO_FORNECEDOR_2099" in trace[0]
        assert trace[1] not in report


def test_web_request_report_is_finished_before_response_returns(
    workspace: Path,
) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        for path, expected in (("/findings", 200), ("/missing-page", 404)):
            response = client.get(path)
            assert response.status_code == expected
            directory = workspace / "runs" / response.headers["X-OPSIU-Run-ID"]
            report = (directory / "report.md").read_text(encoding="utf-8")
            manifest = json.loads((directory / "manifest.json").read_text())
            assert directory.name in report
            assert manifest["finished_at"] is not None
            if expected == 404:
                assert manifest["status"] == "completed_with_errors"
                assert "WebError" in report

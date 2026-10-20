"""Final artifacts preserve current human approval and original evidence."""

import csv
import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import JsonValue, ReviewFilters
from opsiu.application.review import ReviewService
from opsiu.application.runs import Run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("synthetic_cap_policies"),
]
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def invoke(*arguments: str) -> Result:
    from opsiu.interfaces.cli import app

    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == 0, result.output
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
        "[years]\nfrom=2099\nto=2099\n",
        encoding="utf-8",
    )
    invoke("ingest")
    invoke("check", "--regras", "R01,R02,R03,R08,R09")
    invoke("explain", "--regras", "R08")
    return tmp_path


@contextmanager
def service(workspace: Path) -> Iterator[ReviewService]:
    with connect_database(workspace / "db.duckdb") as connection:
        ledger = CompositeLedger(
            JsonlLedger(workspace / "runs"), DuckDBLedger(connection)
        )

        def make_run(command: str, arguments: dict[str, JsonValue]) -> Run:
            return Run(
                ledger=ledger,
                clock=SystemClock(),
                ids=Uuid7Generator(),
                command=command,
                arguments=arguments,
                metadata=collect_metadata("final-report-integration"),
            )

        yield ReviewService(
            DuckDBReviewRepository(connection), make_run, reviewer="Revisora final"
        )


def export(workspace: Path) -> tuple[Path, list[dict[str, str]]]:
    result = invoke("report", "final")
    directory = workspace / "runs" / run_id(result)
    with (directory / "final.csv").open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["command"] == "report final"
    assert len(manifest["steps"]) == 1
    assert manifest["steps"][0]["status"] == "completed"
    for artifact in manifest["artifacts"]:
        path = Path(artifact["path"])
        assert path.resolve().parent == directory
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        assert path.stat().st_size == artifact["size_bytes"]
    return directory, records


def test_final_uses_latest_approval_original_provenance_and_pareceres(
    workspace: Path,
) -> None:
    source = workspace / "input/Ano-2099.json"
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    invoke("explain", "--regras", "R08", "--force")
    invoke("explain", "--regras", "R01,R02")
    invoke("explain", "--consolidar", "--deputado", "101")
    with service(workspace) as review:
        keys = {
            rule: review.list_pending(ReviewFilters(rule_id=rule))[
                0
            ].record.finding.finding_key
            for rule in ("R01", "R02", "R03", "R08", "R09")
        }
        review.decide(keys["R01"], "aprovado", "Aprovação atual.")
        review.decide(keys["R02"], "aprovado", "Aprovação antiga.")
        review.decide(keys["R02"], "revisar", "Evidência pendente.")
        approved = review.decide(
            keys["R08"], "aprovado", "Conferido <script>alert(1)</script>."
        )
        review.decide(keys["R09"], "aprovado", "Documento comparado.")
        review.decide(keys["R03"], "aprovado", "Aprovação antiga.")
        review.decide(keys["R03"], "rejeitado", "Rejeição atual.")
        original = review.get_finding(keys["R08"])
    # Later import must not relabel the source identity of the approved finding.
    data = json.loads(source.read_text())
    data["dados"][39].update(
        numeroDeputadoID=9999, nomeParlamentar="Nome posterior", siglaUF="RJ"
    )
    source.write_text(json.dumps(data), encoding="utf-8")
    invoke("ingest")
    directory, records = export(workspace)
    assert {row["finding_key"] for row in records} == {
        keys["R01"],
        keys["R08"],
        keys["R09"],
    }
    assert {row["source_sha256"] for row in records} == {original_sha}
    assert {row["status"] for row in records} == {"active"}
    assert {row["decision"] for row in records} == {"aprovado"}
    annotated = next(row for row in records if row["rule_id"] == "R08")
    assert annotated["numero_deputado_id"] == "201"
    assert annotated["nome_parlamentar"] == original.record.nome_parlamentar
    assert annotated["run_id"] == original.record.finding.provenance.run_id
    assert annotated["decision_id"] == approved.decision_id
    assert annotated["decision_run_id"] == approved.run_id
    assert annotated["decision_step_id"] == approved.step_id
    assert annotated["reviewer"] == "Revisora final"
    unannotated = next(row for row in records if row["rule_id"] == "R09")
    assert json.loads(unannotated["pareceres_json"]) == []
    # Every selected annotation is attributable to its actual model and trace.
    for annotation in original.annotations:
        assert annotation.parecer_id in json.dumps(annotated)
        assert annotation.agent_trace_id in json.dumps(annotated)
    assert "scripted" in json.dumps(annotated)
    pages = list(directory.glob("deputado-*.md"))
    assert len(pages) == 3
    combined = "\n".join(path.read_text() for path in pages)
    assert all(key in combined for key in (keys["R01"], keys["R08"], keys["R09"]))
    assert all(keys[rule] not in combined for rule in ("R02", "R03"))
    assert "Nome posterior" not in combined
    assert "<script>" not in combined
    assert "simulad" in combined.lower()
    summary = (directory / "final.md").read_text()
    assert "R08" in summary and "R09" in summary
    assert "R01" in summary and "R02" not in summary
    assert "25,00" in summary and "321,09" in summary


def test_final_empty_supersession_and_repeat_preserve_prior_artifacts(
    workspace: Path,
) -> None:
    _, empty = export(workspace)
    assert empty == []
    with service(workspace) as review:
        key = review.list_pending(ReviewFilters(rule_id="R08"))[
            0
        ].record.finding.finding_key
        decision = review.decide(key, "aprovado", "Aprovação preservada.")
    original, approved = export(workspace)
    assert len(approved) == 1
    saved = {path.name: path.read_bytes() for path in original.iterdir()}
    repeated, repeated_rows = export(workspace)
    assert repeated != original
    assert repeated_rows == approved
    (workspace / "input/Ano-2099.json").write_text('{"dados": []}', encoding="utf-8")
    invoke("ingest")
    invoke("check", "--regras", "R08")
    latest, rows = export(workspace)
    assert rows == []
    assert list(latest.glob("deputado-*.md")) == []
    assert all((original / name).read_bytes() == data for name, data in saved.items())
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT decision, status, CAST(decision_id AS VARCHAR) "
            "FROM findings_current WHERE finding_key=?",
            [key],
        ).fetchone() == ("aprovado", "superseded", decision.decision_id)

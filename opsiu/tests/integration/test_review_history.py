"""Human decisions retain original evidence and survive later supersession."""

import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import JsonValue, ReviewFilters
from opsiu.application.review import ReviewService
from opsiu.application.runs import Run

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def invoke(*arguments: str) -> None:
    from opsiu.interfaces.cli import app

    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == 0, result.exception or result.output


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
    invoke("check", "--regras", "R01,R02,R08,R09,R10")
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
                metadata=collect_metadata("review-integration"),
            )

        yield ReviewService(
            DuckDBReviewRepository(connection), make_run, reviewer="Revisora local"
        )


def test_decisions_append_and_each_has_its_own_single_step_run(workspace: Path) -> None:
    with service(workspace) as review:
        pending = review.list_pending(ReviewFilters(rule_id="R08"))
        assert len(pending) == 1
        key = pending[0].record.finding.finding_key
        first = review.decide(key, "aprovado", "Documento conferido.")
        assert review.list_pending(ReviewFilters(rule_id="R08")) == ()
        second = review.decide(key, "rejeitado", "Correção após nova leitura.")
        assert review.list_pending(ReviewFilters(rule_id="R08")) == ()
        third = review.decide(key, "revisar", "Aguardar <documento> complementar.")
        assert len(review.list_pending(ReviewFilters(rule_id="R08"))) == 1
        detail = review.get_finding(key)
        assert detail.current_decision == third
        assert {item.decision_id for item in detail.decisions} == {
            first.decision_id,
            second.decision_id,
            third.decision_id,
        }
        assert len(detail.annotations) == 1
        assert detail.annotations[0].kind == "parecer"
        assert detail.expense.id_documento == detail.record.finding.id_documento

    assert len({first.decision_id, second.decision_id, third.decision_id}) == 3
    assert len({first.run_id, second.run_id, third.run_id}) == 3
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT decision, comment FROM decisions WHERE finding_key=? "
            "ORDER BY decided_at, decision_id",
            [key],
        ).fetchall() == [
            ("aprovado", "Documento conferido."),
            ("rejeitado", "Correção após nova leitura."),
            ("revisar", "Aguardar <documento> complementar."),
        ]
        assert db.execute(
            "SELECT decision, reviewer FROM findings_current WHERE finding_key=?",
            [key],
        ).fetchone() == ("revisar", "Revisora local")
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (1,)
        for decision in (first, second, third):
            assert db.execute(
                "SELECT command, status FROM runs WHERE run_id=?", [decision.run_id]
            ).fetchone() == ("review.decide", "completed")
            assert db.execute(
                "SELECT CAST(step_id AS VARCHAR), status FROM steps WHERE run_id=?",
                [decision.run_id],
            ).fetchall() == [(decision.step_id, "completed")]
            manifest = json.loads(
                (workspace / "runs" / decision.run_id / "manifest.json").read_text()
            )
            assert manifest["status"] == "completed"
            assert len(manifest["steps"]) == 1


def test_supersession_preserves_decisions_and_rejects_new_approval(
    workspace: Path,
) -> None:
    with service(workspace) as review:
        key = review.list_pending(ReviewFilters(rule_id="R08"))[
            0
        ].record.finding.finding_key
        decision = review.decide(key, "aprovado", "Revisão anterior.")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        original = db.execute("SELECT * FROM decisions").fetchall()
    (workspace / "input/Ano-2099.json").write_text('{"dados": []}', encoding="utf-8")
    invoke("ingest")
    invoke("check", "--regras", "R08")
    with service(workspace) as review:
        assert review.list_pending(ReviewFilters(rule_id="R08")) == ()
        detail = review.get_finding(key)
        assert detail.record.finding.status == "superseded"
        assert detail.current_decision == decision
        with pytest.raises(ValueError):
            review.decide(key, "aprovado", "Não pode reativar o achado.")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT * FROM decisions").fetchall() == original
        assert db.execute(
            "SELECT status, decision, CAST(decision_id AS VARCHAR) "
            "FROM findings_current WHERE finding_key=?",
            [key],
        ).fetchone() == ("superseded", "aprovado", decision.decision_id)
        assert db.execute(
            "SELECT count(*) FROM runs WHERE command='review.decide' "
            "AND status='completed_with_errors'"
        ).fetchone() == (1,)


def test_filters_use_original_expense_after_a_new_import(workspace: Path) -> None:
    source = workspace / "input/Ano-2099.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    original = data["dados"][39]["nomeParlamentar"]
    data["dados"][39].update(
        numeroDeputadoID=9999, nomeParlamentar="Nome posterior", siglaUF="RJ"
    )
    source.write_text(json.dumps(data), encoding="utf-8")
    invoke("ingest")
    with service(workspace) as review:
        matches = review.list_pending(
            ReviewFilters(
                rule_id="R08", deputy_id=201, uf="SP", min_amount=Decimal("25")
            )
        )
        assert len(matches) == 1
        assert matches[0].record.nome_parlamentar == original
        detail = review.get_finding(matches[0].record.finding.finding_key)
        assert detail.expense.nome_parlamentar == original
        assert detail.expense.numero_deputado_id == 201
        assert review.list_pending(ReviewFilters(rule_id="R08", deputy_id=9999)) == ()
        assert review.list_pending(ReviewFilters(rule_id="R08", uf="RJ")) == ()
        assert (
            review.list_pending(
                ReviewFilters(rule_id="R08", min_amount=Decimal("25.01"))
            )
            == ()
        )


def test_unknown_finding_failure_is_audited_without_a_decision(workspace: Path) -> None:
    with service(workspace) as review:
        with pytest.raises(LookupError):
            review.decide("f" * 64, "aprovado", "Identidade inexistente.")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM decisions").fetchone() == (0,)
        failed = db.execute(
            "SELECT CAST(run_id AS VARCHAR) FROM runs WHERE command='review.decide'"
        ).fetchall()
        assert len(failed) == 1
        assert db.execute(
            "SELECT status FROM steps WHERE run_id=?", [failed[0][0]]
        ).fetchall() == [("failed",)]
        assert db.execute(
            "SELECT count(*) FROM events WHERE run_id=? AND kind='step.failed'",
            [failed[0][0]],
        ).fetchone() == (1,)

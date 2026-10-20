"""Rule checks skip missing inputs, isolate failures, and protect read-only SQL."""

import json
import os
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
from opsiu.domain.decisions import Decision, DecisionValue
from opsiu.domain.rules import registry
from opsiu.domain.rules.base import RuleDescriptor, RuleResult
from opsiu.interfaces.cli import app
from opsiu.interfaces.cli.runtime import command_run

pytestmark = pytest.mark.integration


class GoodRule:
    descriptor = RuleDescriptor(
        "T01", "1", "Regra sintética", "alta", "Teste", ("despesas_current",)
    )
    sql = (
        "SELECT id_documento, valor_liquido FROM despesas_current ORDER BY id_documento"
    )

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        document, amount = row["id_documento"], row["valor_liquido"]
        assert isinstance(document, int) and isinstance(amount, Decimal)
        return RuleResult(document, amount, {"valor": amount})


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="database.duckdb"\nruns_dir="runs"\n',
        encoding="utf-8",
    )
    records = [
        {
            "nomeParlamentar": "Deputada Sintética",
            "siglaUF": "DF",
            "numeroSubCota": 3,
            "valorDocumento": "10.01",
            "valorGlosa": "0",
            "valorLiquido": "10.01",
            "ano": 2099,
            "mes": 1,
            "numeroDeputadoID": 1,
            "idDocumento": document,
        }
        for document in (1, 2)
    ]
    (tmp_path / "input/Ano-2099.json").write_text(json.dumps({"dados": records}))
    monkeypatch.setattr(registry, "RULES", (GoodRule,))
    return tmp_path


def load() -> None:
    result = CliRunner().invoke(app, ["ingest", "--anos", "2099"])
    assert result.exit_code == 0, result.exception or result.output


def test_empty_registry_completes_with_no_finding_mutations(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "RULES", ())
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0, result.exception or result.output
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM findings").fetchone() == (0,)
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    manifest = json.loads((workspace / "runs" / run_id / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["counters"]["rules_selected"] == 0


@pytest.mark.parametrize("selection", ["T99", "T01,T01", "", "T01,"])
def test_invalid_rule_selection_aborts_before_database_creation(
    workspace: Path,
    selection: str,
) -> None:
    result = CliRunner().invoke(app, ["check", "--regras", selection])
    assert result.exit_code == 2
    assert not (workspace / "database.duckdb").exists()
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    manifest = json.loads((workspace / "runs" / run_id / "manifest.json").read_text())
    assert manifest["status"] == "aborted"


def test_missing_required_relation_skips_without_superseding_findings(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load()
    assert CliRunner().invoke(app, ["check"]).exit_code == 0
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        original = db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
    monkeypatch.setattr(
        GoodRule,
        "descriptor",
        replace(GoodRule.descriptor, needs=("missing_external",)),
    )
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 0
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
            == original
        )
        assert db.execute(
            "SELECT status FROM steps WHERE run_id=? AND name='verificar_T01'", [run_id]
        ).fetchone() == ("skipped",)


def test_evaluation_failure_preserves_old_results_and_continues_other_rules(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingRule(GoodRule):
        descriptor = replace(GoodRule.descriptor, version="2")

        def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
            if row["id_documento"] == 2:
                raise ValueError("falha sintética após o primeiro candidato")
            return super().evaluate(row)

    class OtherRule(GoodRule):
        descriptor = replace(GoodRule.descriptor, id="T02")

    load()
    assert CliRunner().invoke(app, ["check"]).exit_code == 0
    monkeypatch.setattr(registry, "RULES", (FailingRule, OtherRule))
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 1
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT rule_id, rule_version, status, count(*) "
            "FROM findings GROUP BY ALL ORDER BY 1"
        ).fetchall() == [("T01", "1", "active", 2), ("T02", "1", "active", 2)]
        sql_ids = {
            str(row[0])
            for row in db.execute(
                "SELECT event_id FROM events WHERE run_id=?", [run_id]
            ).fetchall()
        }
    events = [
        json.loads(line)
        for line in (workspace / "runs" / run_id / "events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert {event["event_id"] for event in events} == sql_ids
    assert any(event["kind"] == "step.failed" for event in events)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM despesas",
        "SELECT 1; DELETE FROM despesas",
        "SELECT 1 AS duplicate, 2 AS duplicate",
    ],
)
def test_candidate_query_rejects_mutations_multiple_statements_and_ambiguous_columns(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
) -> None:
    load()
    monkeypatch.setattr(GoodRule, "sql", sql)
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code == 1
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM despesas").fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM findings").fetchone() == (0,)


def test_explicit_suspension_retires_evidence_once_and_preserves_human_history(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    load()
    runner = CliRunner()
    assert runner.invoke(app, ["check"]).exit_code == 0
    with command_run("test.review", {}) as context:
        with context.run.step("decidir"):
            db = context.open_database()
            assert db is not None
            first = db.execute("SELECT finding_key FROM findings LIMIT 1").fetchone()
            assert first is not None
            key = str(first[0])
            DuckDBReviewRepository(db).append_decision(
                Decision(
                    context.run.ids.new_id(),
                    key,
                    DecisionValue.REJECTED,
                    "Revisor sintético",
                    "Decisão anterior preservada.",
                    context.run.clock.now(),
                    context.run.run_id,
                    context.run.require_step(),
                )
            )
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        records = db.execute(
            "SELECT * FROM finding_records ORDER BY finding_key"
        ).fetchall()
        decisions = db.execute(
            "SELECT * FROM decisions ORDER BY decision_id"
        ).fetchall()
    reason = "Datas necessárias indisponíveis; inferência anterior retirada."
    monkeypatch.setattr(
        GoodRule,
        "descriptor",
        replace(
            GoodRule.descriptor,
            version="2",
            needs=("missing_external",),
            suspended_reason=reason,
        ),
    )
    # Neither dry-run nor publication may execute candidate SQL for suspension.
    monkeypatch.setattr(GoodRule, "sql", "DELETE FROM despesas")
    assert runner.invoke(app, ["check", "--dry-run"]).exit_code == 0
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT count(*) FROM findings WHERE status='active'"
        ).fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM finding_supersessions").fetchone() == (
            0,
        )
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    assert runner.invoke(app, ["check"]).exit_code == 0
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM finding_records ORDER BY finding_key").fetchall()
            == records
        )
        assert (
            db.execute("SELECT * FROM decisions ORDER BY decision_id").fetchall()
            == decisions
        )
        assert db.execute("SELECT count(*) FROM finding_supersessions").fetchone() == (
            2,
        )
        assert db.execute(
            "SELECT count(*) FROM findings WHERE status='active'"
        ).fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM despesas").fetchone() == (2,)
        event = db.execute(
            "SELECT attributes_json FROM events "
            "WHERE run_id=? AND kind='rule.suspended'",
            [run_id],
        ).fetchone()
        assert event is not None
        assert json.loads(event[0]) == {
            "rule_id": "T01",
            "rule_version": "2",
            "reason": reason,
            "findings_superseded": 2,
            "dry_run": False,
        }
    manifest = json.loads((workspace / "runs" / run_id / "manifest.json").read_text())
    assert manifest["counters"]["rules_suspended"] == 1
    assert manifest["counters"]["rules_completed"] == 0
    assert manifest["counters"]["findings_superseded"] == 2


def test_partial_policy_coverage_preserves_the_complete_previous_result(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class PartlyCoveredRule(GoodRule):
        descriptor = replace(GoodRule.descriptor, version="2")

        def coverage_reason(self, row: Mapping[str, object]) -> str | None:
            return (
                "Competência sem política pesquisada."
                if row["id_documento"] == 2
                else None
            )

    load()
    runner = CliRunner()
    assert runner.invoke(app, ["check"]).exit_code == 0
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        original = db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
    monkeypatch.setattr(registry, "RULES", (PartlyCoveredRule,))
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
            == original
        )
        assert db.execute("SELECT count(*) FROM finding_supersessions").fetchone() == (
            0,
        )
        assert db.execute(
            "SELECT status FROM steps WHERE run_id=? AND name='verificar_T01'",
            [run_id],
        ).fetchone() == ("skipped",)
    manifest = json.loads((workspace / "runs" / run_id / "manifest.json").read_text())
    assert manifest["counters"]["candidates_without_coverage"] == 1
    assert manifest["counters"]["rules_skipped"] == 1
    assert manifest["counters"]["rules_completed"] == 0

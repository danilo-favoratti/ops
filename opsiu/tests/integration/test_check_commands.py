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
from opsiu.domain.rules import registry
from opsiu.domain.rules.base import RuleDescriptor, RuleResult
from opsiu.interfaces.cli import app

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

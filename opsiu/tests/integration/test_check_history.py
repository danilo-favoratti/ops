"""Finding history survives retries, refreshed inputs, dry runs, and conflicts."""

import json
import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.domain.rules import registry
from opsiu.domain.rules.base import RuleDescriptor, RuleResult
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration


class HistoryRule:
    descriptor = RuleDescriptor("T99", "1", "Histórico", "revisar", "Teste")
    sql = (
        "SELECT id_documento, valor_liquido FROM despesas_current ORDER BY id_documento"
    )

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        document, amount = row["id_documento"], row["valor_liquido"]
        assert isinstance(document, int)
        assert isinstance(amount, Decimal)
        return RuleResult(document, amount, {"total": amount})


@pytest.fixture
def history_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir = "input"\ndb_path = "opsiu.duckdb"\n'
        'runs_dir = "runs"\nexternal_dir = "external"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "RULES", (HistoryRule,))
    monkeypatch.setattr(
        HistoryRule,
        "descriptor",
        RuleDescriptor("T99", "1", "Histórico", "revisar", "Teste"),
    )
    return tmp_path


def load(root: Path, rows: list[tuple[int, str]]) -> None:
    records = [
        {
            "nomeParlamentar": "Parlamentar Sintético",
            "cpf": "00000000001",
            "siglaUF": "DF",
            "siglaPartido": "TESTE",
            "numeroSubCota": 3,
            "fornecedor": "Fornecedor Sintético",
            "cnpjCPF": "00000000000001",
            "numero": f"NF-{document}",
            "dataEmissao": "2099-01-05T00:00:00",
            "valorDocumento": amount,
            "valorGlosa": "0",
            "valorLiquido": amount,
            "mes": 1,
            "ano": 2099,
            "numeroDeputadoID": 101,
            "idDocumento": document,
        }
        for document, amount in rows
    ]
    (root / "input/Ano-2099.json").write_text(
        json.dumps({"dados": records}), encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["ingest", "--anos", "2099"])
    assert result.exit_code == 0, result.exception or result.output


def check(*args: str, expected: int = 0) -> Result:
    result = CliRunner().invoke(app, ["check", "--regras", "T99", *args])
    assert result.exit_code == expected, result.exception or result.output
    return result


def rows(
    root: Path, sql: str = "SELECT * FROM findings ORDER BY finding_key"
) -> list[tuple[Any, ...]]:
    with connect_database(root / "opsiu.duckdb", read_only=True) as connection:
        return connection.execute(sql).fetchall()


def test_retry_dry_run_version_and_disappearing_results(history_root: Path) -> None:
    root = history_root
    load(root, [(1, "100.00"), (2, "200.00")])
    check()
    original = rows(root)
    assert len(original) == 2
    check()
    assert rows(root) == original
    forced = CliRunner().invoke(app, ["ingest", "--anos", "2099", "--force"])
    assert forced.exit_code == 0, forced.exception or forced.output
    check()
    assert rows(root) == original
    assert rows(root, "SELECT count(*) FROM source_files") == [(2,)]

    HistoryRule.descriptor = RuleDescriptor("T99", "2", "Histórico", "revisar", "Teste")
    check("--dry-run")
    assert rows(root) == original
    check()
    assert rows(
        root,
        "SELECT rule_version, status, count(*) FROM findings GROUP BY ALL ORDER BY 1",
    ) == [
        ("1", "superseded", 2),
        ("2", "active", 2),
    ]
    load(root, [])
    check()
    assert rows(root, "SELECT status, count(*) FROM findings GROUP BY status") == [
        ("superseded", 4)
    ]
    assert rows(
        root,
        "SELECT count(*) FROM findings WHERE superseded_at IS NULL "
        "OR superseded_by_run_id IS NULL",
    ) == [(0,)]


def test_changed_evidence_conflict_preserves_whole_previous_result(
    history_root: Path,
) -> None:
    root = history_root
    load(root, [(2, "100.00"), (3, "200.00")])
    check()
    original = rows(root)
    load(root, [(1, "50.00"), (2, "101.00")])
    result = check(expected=1)
    assert rows(root) == original
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    manifest = json.loads((root / "runs" / run_id / "manifest.json").read_text())
    assert manifest["status"] == "completed_with_errors"
    assert rows(
        root,
        "SELECT count(*) FROM findings f JOIN despesas d "
        "ON d.source_file_id = f.source_file_id AND d.id_documento = f.id_documento "
        "AND d.record_sha256 = f.record_sha256",
    ) == [(2,)]


def test_superseded_finding_cannot_silently_recur(history_root: Path) -> None:
    root = history_root
    load(root, [(1, "100.00")])
    check()
    load(root, [])
    check()
    superseded = rows(root)
    load(root, [(1, "100.00")])
    check(expected=1)
    assert rows(root) == superseded


def test_ambiguous_document_origin_fails_without_publishing(history_root: Path) -> None:
    root = history_root
    load(root, [(1, "100.00"), (1, "200.00")])
    check(expected=1)
    assert rows(root) == []


def test_combined_document_occurrences_preserve_provenance_and_replay(
    history_root: Path,
) -> None:
    HistoryRule.descriptor = RuleDescriptor(
        "T99", "2", "Histórico", "revisar", "Teste", combine_document_rows=True
    )
    load(history_root, [(1, "100.00"), (1, "200.00"), (2, "50.00")])
    check()
    original = rows(history_root)
    result = rows(
        history_root, "SELECT amount, evidence_json FROM findings WHERE id_documento=1"
    )
    assert result[0][0] == Decimal("300.00")
    evidence = json.loads(result[0][1])
    assert len(evidence["ocorrencias"]) == 2
    assert len(evidence["registros_origem"]) == 2
    hashes = {item["registro_sha256"] for item in evidence["registros_origem"]}
    assert hashes == {
        row[0]
        for row in rows(
            history_root,
            "SELECT record_sha256 FROM despesas_current WHERE id_documento=1",
        )
    }
    check()
    assert rows(history_root) == original
    forced = CliRunner().invoke(app, ["ingest", "--anos", "2099", "--force"])
    assert forced.exit_code == 0
    check()
    assert rows(history_root) == original
    exported = CliRunner().invoke(app, ["report", "findings"])
    assert exported.exit_code == 0, exported.output


def test_real_cap_and_duplicate_rules_accept_accounting_rows(
    history_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opsiu.domain.rules.r01_locacao_veiculos import R01LocacaoVeiculos
    from opsiu.domain.rules.r09_nota_duplicada import R09NotaDuplicada

    monkeypatch.setattr(registry, "RULES", (R01LocacaoVeiculos, R09NotaDuplicada))
    load(history_root, [(1, "13000.00"), (2, "13000.00"), (2, "0.00")])
    path = history_root / "input/Ano-2099.json"
    data = json.loads(path.read_text())
    for row in data["dados"]:
        row["numeroSubCota"] = 120
        row["numero"] = "MESMA-NOTA"
        row["valorDocumento"] = "13000.00"
    path.write_text(json.dumps(data))
    runner = CliRunner()
    assert runner.invoke(app, ["ingest", "--anos", "2099"]).exit_code == 0
    result = runner.invoke(app, ["check", "--regras", "R01,R09"])
    assert result.exit_code == 0, result.output
    assert rows(
        history_root,
        "SELECT rule_id, rule_version, id_documento, amount "
        "FROM findings ORDER BY rule_id,id_documento",
    ) == [
        ("R01", "2", 2, Decimal("13287.00")),
        ("R09", "3", 1, Decimal("13000.00")),
        ("R09", "3", 2, Decimal("13000.00")),
    ]
    for (encoded,) in rows(
        history_root, "SELECT evidence_json FROM findings WHERE id_documento=2"
    ):
        assert len(json.loads(encoded)["registros_origem"]) == 2
    original = rows(history_root)
    assert runner.invoke(app, ["check", "--regras", "R01,R09"]).exit_code == 0
    assert rows(history_root) == original

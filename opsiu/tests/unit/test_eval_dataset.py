"""Synthetic evaluation labels remain strict and reproducible from real rules."""

import json
import os
from collections import Counter
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from typer.testing import CliRunner

from evals.dataset import (
    DEPUTY_ID_STRIDE,
    DOCUMENT_ID_STRIDE,
    FIXTURE_PATH,
    LOCAL_RULE_IDS,
    REPLICA_COUNT,
    build_expense_rows,
    load_cases,
)
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.domain.models import Despesa
from opsiu.interfaces.cli import app


def _case_json(**changes: object) -> str:
    case: dict[str, object] = {
        "finding_key": "a" * 64,
        "expected_recommendation": "revisar",
        "notes": "Hipótese sintética: falta contexto para confirmar o sinal.",
    }
    case.update(changes)
    return json.dumps(case, ensure_ascii=False)


@pytest.mark.unit
def test_default_cases_are_frozen_diverse_and_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    cases = load_cases()
    assert len(cases) == len({case.finding_key for case in cases}) == 50
    assert Counter(case.expected_recommendation for case in cases) == {
        "manter": 26,
        "revisar": 24,
    }
    assert all(
        "Hipótese sintética sem validação humana" in case.notes for case in cases
    )
    with pytest.raises(FrozenInstanceError):
        setattr(cases[0], "notes", "altered")


@pytest.mark.unit
def test_replication_preserves_fixture_values_and_isolates_deputies_and_invoices() -> (
    None
):
    original = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["dados"]
    rows = build_expense_rows()
    assert len(rows) == 800
    expenses = tuple(Despesa.from_raw(row) for row in rows)
    assert len({expense.id_documento for expense in expenses}) == 800
    assert len({expense.numero_deputado_id for expense in expenses}) == 60
    for replica in range(REPLICA_COUNT):
        for offset, row in enumerate(rows[replica * 200 : (replica + 1) * 200]):
            expected = dict(original[offset])
            document = int(expected["idDocumento"]) + replica * DOCUMENT_ID_STRIDE
            expected["idDocumento"] = str(document)
            expected["numeroDeputadoID"] += replica * DEPUTY_ID_STRIDE
            expected["numero"] = f"EVAL-{replica + 1}-{expected['numero']}"
            expected["urlDocumento"] = f"https://example.invalid/opsiu/{document}.pdf"
            assert row == expected
    invoice_counts = Counter(expense.numero for expense in expenses)
    assert {number: count for number, count in invoice_counts.items() if count > 1} == {
        f"EVAL-{replica + 1}-R09-DUPLICADA": 2 for replica in range(REPLICA_COUNT)
    }
    rows[0]["numero"] = "caller mutation"
    assert build_expense_rows()[0]["numero"] == "EVAL-1-R01-ACIMA-A"
    assert json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["dados"] == original


@pytest.mark.unit
@pytest.mark.parametrize(
    "document",
    [
        "",
        "\n",
        "{",
        "[]",
        "null",
        "{}",
        _case_json(finding_key="A" * 64),
        _case_json(finding_key="a" * 63),
        _case_json(finding_key="g" * 64),
        _case_json(finding_key=123),
        _case_json(expected_recommendation="aprovar"),
        _case_json(expected_recommendation=True),
        _case_json(notes="  "),
        _case_json(notes=None),
        _case_json(extra="unrecognized"),
        _case_json()[:-1] + ', "notes": "duplicate field"}',
        _case_json() + "\n" + _case_json(),
        _case_json() + "\n\n",
    ],
)
def test_malformed_or_ambiguous_cases_fail_closed(
    tmp_path: Path, document: str
) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


@pytest.mark.unit
def test_explicit_dataset_preserves_order_and_supports_all_contract_labels(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(
            _case_json(finding_key=character * 64, expected_recommendation=label)
            for character, label in (
                ("c", "descartar"),
                ("b", "manter"),
                ("a", "revisar"),
            )
        ),
        encoding="utf-8",
    )
    cases = load_cases(path)
    assert [case.finding_key for case in cases] == ["c" * 64, "b" * 64, "a" * 64]
    assert [case.expected_recommendation for case in cases] == [
        "descartar",
        "manter",
        "revisar",
    ]


@pytest.mark.unit
def test_dataset_requires_valid_utf8_and_reports_invalid_line(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        load_cases(path)
    path.write_text(_case_json() + "\n{}", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_cases(path)


@pytest.mark.integration
def test_real_rules_reproduce_sixty_findings_and_all_fifty_selected_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "0")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    (tmp_path / "input/Ano-2099.json").write_text(
        json.dumps({"dados": build_expense_rows()}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        "[years]\nfrom=2099\nto=2099\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    for arguments in (["ingest"], ["check", "--regras", ",".join(LOCAL_RULE_IDS)]):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.exception or result.output
    with connect_database(tmp_path / "db.duckdb", read_only=True) as connection:
        findings = connection.execute(
            "SELECT finding_key, rule_id, id_documento FROM findings "
            "WHERE status = 'active'"
        ).fetchall()
        assert connection.execute("SELECT count(*) FROM despesas").fetchone() == (800,)
    assert len(findings) == 60
    assert Counter(row[1] for row in findings) == {
        **{f"R{number:02d}": 4 for number in range(1, 7)},
        "R07": 16,
        "R08": 4,
        "R09": 8,
        "R10": 4,
        "R14": 4,
    }
    selected = sorted(
        findings,
        key=lambda row: ((row[2] - 209900001) // DOCUMENT_ID_STRIDE, row[1], row[2]),
    )[:50]
    assert tuple(row[0] for row in selected) == tuple(
        case.finding_key for case in load_cases()
    )
    assert {row[1] for row in selected} == set(LOCAL_RULE_IDS)

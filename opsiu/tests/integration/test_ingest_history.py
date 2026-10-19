"""Reloads preserve expense history while atomically changing the current snapshot."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    config = tmp_path / "opsiu.toml"
    config.write_text(
        '[paths]\ndata_dir = "input"\ndb_path = "db/opsiu.duckdb"\n'
        'runs_dir = "runs"\nexternal_dir = "external"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPSIU_CONFIG", str(config))
    return tmp_path


def expense(document: int, amount: str = "100.00") -> dict[str, object]:
    return {
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


def write_source(root: Path, records: list[dict[str, object]]) -> None:
    (root / "input" / "Ano-2099.json").write_text(
        json.dumps({"dados": records}, ensure_ascii=False), encoding="utf-8"
    )


def invoke_ingest(*extra: str) -> Result:
    result = CliRunner().invoke(app, ["ingest", "--anos", "2099", *extra])
    assert result.exit_code == 0, result.exception or result.output
    return result


def test_unchanged_force_and_changed_source_preserve_history(
    isolated_ingest: Path,
) -> None:
    root = isolated_ingest
    write_source(root, [expense(1)])
    invoke_ingest()
    with connect_database(root / "db/opsiu.duckdb", read_only=True) as connection:
        original = connection.execute("SELECT * FROM source_files").fetchall()
        original_expenses = connection.execute("SELECT * FROM despesas").fetchall()

    invoke_ingest()
    with connect_database(root / "db/opsiu.duckdb", read_only=True) as connection:
        assert connection.execute("SELECT * FROM source_files").fetchall() == original
        assert (
            connection.execute("SELECT * FROM despesas").fetchall() == original_expenses
        )

    invoke_ingest("--force")
    write_source(root, [expense(2, "200.00")])
    invoke_ingest()
    with connect_database(root / "db/opsiu.duckdb", read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM source_files").fetchone() == (
            3,
        )
        assert connection.execute("SELECT count(*) FROM despesas").fetchone() == (3,)
        assert connection.execute(
            "SELECT id_documento FROM despesas_current"
        ).fetchall() == [(2,)]
        assert connection.execute("SELECT count(*) FROM deputados").fetchone() == (1,)
        first_id = original[0][0]
        assert (
            connection.execute(
                "SELECT * FROM source_files WHERE source_file_id = ?", [first_id]
            ).fetchall()
            == original
        )
        assert (
            connection.execute(
                "SELECT * FROM despesas WHERE source_file_id = ?", [first_id]
            ).fetchall()
            == original_expenses
        )


def test_empty_replacement_changes_current_view_and_keeps_history(
    isolated_ingest: Path,
) -> None:
    root = isolated_ingest
    write_source(root, [expense(1)])
    invoke_ingest()
    write_source(root, [])
    invoke_ingest()

    with connect_database(root / "db/opsiu.duckdb", read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM despesas_current"
        ).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM deputados").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM despesas").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM source_files").fetchone() == (
            2,
        )


def test_late_invalid_record_rolls_back_staged_batches_and_retains_current_source(
    isolated_ingest: Path,
) -> None:
    root = isolated_ingest
    write_source(root, [expense(1)])
    invoke_ingest()
    records = [expense(number) for number in range(2, 10_003)]
    records.append(expense(10_003, "invalid"))
    write_source(root, records)

    result = CliRunner().invoke(app, ["ingest", "--anos", "2099"])

    assert result.exit_code == 1
    with connect_database(root / "db/opsiu.duckdb", read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM source_files").fetchone() == (
            1,
        )
        assert connection.execute("SELECT id_documento FROM despesas").fetchall() == [
            (1,)
        ]
        assert connection.execute(
            "SELECT id_documento FROM despesas_current"
        ).fetchall() == [(1,)]
        run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
        assert connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", [run_id]
        ).fetchone() == ("completed_with_errors",)
        sql_event_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT event_id FROM events WHERE run_id = ?", [run_id]
            ).fetchall()
        }
    events = [
        json.loads(line)
        for line in (root / "runs" / run_id / "events.jsonl").read_text().splitlines()
    ]
    assert {event["event_id"] for event in events} == sql_event_ids
    assert any(event["kind"] == "step.failed" for event in events)

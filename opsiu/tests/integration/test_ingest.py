"""Synthetic yearly ingestion keeps typed values, provenance and failure isolation."""

import hashlib
import json
import os
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.repository import DuckDBExpenseRepository
from opsiu.domain.models import Despesa

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    shutil.copyfile(FIXTURE, tmp_path / "input/Ano-2099.json")
    configuration = tmp_path / "opsiu.toml"
    configuration.write_text(
        '[paths]\ndata_dir="input"\ndb_path="database.duckdb"\n'
        'runs_dir="runs"\n[years]\nfrom=2099\nto=2099\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPSIU_CONFIG", str(configuration))
    return tmp_path


def test_fixture_ingestion_is_typed_and_has_complete_run_and_source_origins(
    workspace: Path,
) -> None:
    from opsiu.interfaces.cli import app

    result = CliRunner().invoke(app, ["ingest"])
    assert result.exit_code == 0, result.exception or result.output
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    source = workspace / "input/Ano-2099.json"
    raw_records = json.loads(source.read_text(encoding="utf-8"))["dados"]
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM despesas_current").fetchone() == (200,)
        assert db.execute("SELECT count(*) FROM deputados").fetchone() == (15,)
        assert db.execute(
            "SELECT numero_deputado_id FROM deputados WHERE lideranca"
        ).fetchall() == [(1001,)]
        assert db.execute(
            "SELECT sum(valor_liquido) FROM despesas_current"
        ).fetchone() == (Decimal("341754.57"),)
        origins = db.execute(
            "SELECT DISTINCT CAST(d.run_id AS VARCHAR), s.name, f.source_year, "
            "f.sha256, f.size FROM despesas d JOIN steps s USING (step_id) "
            "JOIN source_files f USING (source_file_id)"
        ).fetchall()
        assert origins == [
            (
                run_id,
                "ingerir_2099",
                2099,
                hashlib.sha256(source.read_bytes()).hexdigest(),
                source.stat().st_size,
            )
        ]
        columns = [row[0] for row in db.execute("DESCRIBE despesas").fetchall()]
        stored = db.execute(
            "SELECT * FROM despesas ORDER BY source_record_number"
        ).fetchall()
        for raw, row in zip(raw_records, stored, strict=True):
            expense = Despesa.from_raw(raw)
            record = dict(zip(columns, row, strict=True))
            for name in columns[4:]:
                assert record[name] == getattr(expense, name)
        for table, count in (("caps", 6), ("cota_uf", 27), ("adicionais", 14)):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (count,)
    manifest = json.loads((workspace / "runs" / run_id / "manifest.json").read_text())
    assert manifest["counters"]["files_loaded"] == 1
    assert manifest["counters"]["records_loaded"] == 200
    assert manifest["source_file_hashes"] == {
        "input/Ano-2099.json": hashlib.sha256(source.read_bytes()).hexdigest()
    }


@pytest.mark.parametrize("failure", ["missing", "wrong_year", "truncated"])
def test_failed_year_does_not_prevent_later_year_and_leaves_no_stage(
    workspace: Path,
    failure: str,
) -> None:
    from opsiu.interfaces.cli import app

    bad = workspace / "input/Ano-2098.json"
    if failure == "wrong_year":
        shutil.copyfile(workspace / "input/Ano-2099.json", bad)
    elif failure == "truncated":
        bad.write_text('{"dados":[', encoding="utf-8")
    result = CliRunner().invoke(app, ["ingest", "--anos", "2098-2099"])
    assert result.exit_code == 1
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute("SELECT source_year FROM source_files").fetchall() == [
            (2099,)
        ]
        assert db.execute("SELECT count(*) FROM despesas_current").fetchone() == (200,)
        assert db.execute(
            "SELECT name, status FROM steps WHERE run_id=? AND name LIKE 'ingerir_%' "
            "ORDER BY name",
            [run_id],
        ).fetchall() == [("ingerir_2098", "failed"), ("ingerir_2099", "completed")]
        assert not db.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name LIKE 'ingest_stage_%'"
        ).fetchall()


def test_repository_rejects_mutation_without_an_active_matching_step(
    workspace: Path,
) -> None:
    from opsiu.interfaces.cli import app

    assert CliRunner().invoke(app, ["ingest"]).exit_code == 0
    with connect_database(workspace / "database.duckdb") as db:
        identifiers = db.execute("SELECT run_id, step_id FROM steps LIMIT 1").fetchone()
        assert identifiers is not None
        run_id, step_id = identifiers
        repository = DuckDBExpenseRepository(db)
        with pytest.raises(RuntimeError, match="matching active run and step"):
            with repository.stage(
                source_file_id=str(uuid4()),
                source_year=2099,
                run_id=str(run_id),
                step_id=str(step_id),
            ):
                pytest.fail("closed run must not enter a staging transaction")


@pytest.mark.parametrize("years", ["2026-2023", "0", "10000", "x", "2023-2024-2025"])
def test_invalid_year_selection_is_a_durable_aborted_run(
    workspace: Path, years: str
) -> None:
    from opsiu.interfaces.cli import app

    result = CliRunner().invoke(app, ["ingest", "--anos", years])
    assert result.exit_code == 2
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    manifest = json.loads((workspace / "runs" / run_id / "manifest.json").read_text())
    assert manifest["status"] == "aborted"
    assert not (workspace / "database.duckdb").exists()


def test_batch_binding_preserves_large_exact_money_dates_and_nulls(
    workspace: Path,
) -> None:
    from opsiu.interfaces.cli import app

    source = workspace / "input/Ano-2099.json"
    record = json.loads(source.read_text())["dados"][0]
    record.update(
        {
            "valorDocumento": "90071992547409.91",
            "valorLiquido": "90071992547409.91",
            "restituicao": "0.01",
            "datPagamentoRestituicao": "2099-12-31T00:00:00",
            "lote": "",
            "numero": "00000001",
        }
    )
    source.write_text(json.dumps({"dados": [record]}), encoding="utf-8")
    result = CliRunner().invoke(app, ["ingest"])
    assert result.exit_code == 0, result.exception or result.output
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT valor_documento, valor_liquido, restituicao, "
            "data_pagamento_restituicao, lote, numero FROM despesas_current"
        ).fetchone() == (
            Decimal("90071992547409.91"),
            Decimal("90071992547409.91"),
            Decimal("0.01"),
            date(2099, 12, 31),
            None,
            "00000001",
        )


def test_strict_batch_cast_rejects_integer_overflow_without_publishing(
    workspace: Path,
) -> None:
    from opsiu.interfaces.cli import app

    source = workspace / "input/Ano-2099.json"
    record = json.loads(source.read_text())["dados"][0]
    record["idDocumento"] = 2**70
    source.write_text(json.dumps({"dados": [record]}), encoding="utf-8")
    result = CliRunner().invoke(app, ["ingest"])
    assert result.exit_code == 1
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM source_files").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM despesas").fetchone() == (0,)

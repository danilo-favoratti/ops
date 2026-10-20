"""Synthetic yearly ingestion keeps typed values, provenance and failure isolation."""

import hashlib
import json
import os
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import duckdb
import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb import migration_runner
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
from opsiu.adapters.duckdb.repository import DuckDBExpenseRepository
from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
from opsiu.domain.models import Despesa
from opsiu.domain.record_hash import record_sha256

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def test_null_glosa_survives_ingestion_exports_review_and_agent_trace(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opsiu.interfaces.cli import app

    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "0")
    source = workspace / "input/Ano-2099.json"
    base = json.loads(source.read_text())["dados"][39]
    rows = [
        {**base, "valorGlosa": None},
        {**base, "idDocumento": 999001, "valorGlosa": None, "valorLiquido": "-25"},
        {**base, "idDocumento": 999002, "valorGlosa": "0.00"},
    ]
    source.write_text(json.dumps({"dados": rows}), encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    runner = CliRunner()
    for arguments in (
        ["ingest"],
        ["check", "--regras", "R08"],
        ["explain", "--regras", "R08"],
        ["report", "findings"],
    ):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.exception or result.output
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        stored = db.execute(
            "SELECT valor_glosa, valor_liquido, record_sha256 FROM despesas_current "
            "ORDER BY source_record_number"
        ).fetchall()
        assert stored == [
            (None, Decimal("25.00"), record_sha256(rows[0])),
            (None, Decimal("-25.00"), record_sha256(rows[1])),
            (Decimal("0.00"), Decimal("25.00"), record_sha256(rows[2])),
        ]
        assert db.execute("SELECT sha256 FROM source_files").fetchone() == (
            source_hash,
        )
        records = tuple(DuckDBFindingExportReader(db).active_findings())
        unknown = next(
            r for r in records if r.finding.id_documento == int(base["idDocumento"])
        )
        assert unknown.valor_glosa is None
        detail = DuckDBReviewRepository(db).get_finding(unknown.finding.finding_key)
        assert detail is not None
        assert detail.expense.valor_glosa is None
        traces = db.execute(
            "SELECT input_json, input_schema, prompt_version FROM agent_traces "
            "WHERE finding_key = ?",
            [unknown.finding.finding_key],
        ).fetchall()
        assert len(traces) == 1
        assert json.loads(traces[0][0])["registro"]["valor_glosa"] is None
        assert traces[0][1:] == ("opsiu.parecer-request.v2", "3")
    repeat = runner.invoke(app, ["ingest"])
    assert repeat.exit_code == 0
    rows[0]["valorGlosa"] = "malformed"
    source.write_text(json.dumps({"dados": rows}), encoding="utf-8")
    assert runner.invoke(app, ["ingest"]).exit_code == 1
    with connect_database(workspace / "database.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM source_files").fetchone() == (1,)
        assert db.execute("SELECT count(*) FROM despesas_current").fetchone() == (3,)


@pytest.mark.parametrize("failure", [False, True])
def test_nullable_glosa_migration_preserves_existing_audit_history_atomically(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    from opsiu.interfaces.cli import app

    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "0")
    load_migrations = migration_runner.load_migrations
    migrations = tuple(
        item for item in load_migrations() if item.migration_id <= "0010_nullable_glosa"
    )
    monkeypatch.setattr(
        migration_runner,
        "load_migrations",
        lambda directory=None: (
            migrations if directory is None else load_migrations(directory)
        ),
    )
    assert migrations[-1].migration_id == "0010_nullable_glosa"
    with monkeypatch.context() as legacy:
        legacy.setattr(
            migration_runner, "load_migrations", lambda directory=None: migrations[:-1]
        )
        for arguments in (
            ["ingest"],
            ["check", "--regras", "R08"],
            ["explain", "--regras", "R08"],
        ):
            result = CliRunner().invoke(app, arguments)
            assert result.exit_code == 0, result.exception or result.output
    with connect_database(workspace / "database.duckdb") as db:
        tables = (
            "despesas",
            "deputados",
            "source_files",
            "finding_records",
            "pareceres",
            "agent_traces",
            "agent_trace_records",
            "runs",
            "steps",
        )
        before = {
            table: db.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
            for table in tables
        }
        if failure:
            broken = workspace / "broken-migrations"
            broken.mkdir()
            (broken / "0010_nullable_glosa.sql").write_text(
                migrations[-1].sql + "\nSELECT * FROM deliberately_missing_table;"
            )
            with pytest.raises(duckdb.CatalogException):
                migration_runner.apply_migrations(db, directory=broken)
            assert db.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name='despesas' AND column_name='valor_glosa'"
            ).fetchone() == ("NO",)
            assert db.execute("SELECT count(*) FROM despesas_current").fetchone() == (
                200,
            )
        assert migration_runner.apply_migrations(db) == ("0010_nullable_glosa",)
        assert migration_runner.apply_migrations(db) == ()
        assert db.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='despesas' AND column_name='valor_glosa'"
        ).fetchone() == ("YES",)
        assert db.execute("SELECT count(*) FROM despesas_current").fetchone() == (200,)
        assert db.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE table_name='despesas'"
        ).fetchone() == (3,)
        for table in tables:
            assert (
                db.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
                == before[table]
            )


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

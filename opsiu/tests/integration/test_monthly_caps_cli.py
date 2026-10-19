"""The six cap rules run against current ingestion through the public CLI."""

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def test_six_monthly_caps_aggregate_current_records_and_retain_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    shutil.copyfile(FIXTURE, tmp_path / "input/Ano-2099.json")
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        "[years]\nfrom=2099\nto=2099\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    ingest = runner.invoke(app, ["ingest"])
    assert ingest.exit_code == 0, ingest.exception or ingest.output
    selected = ",".join(f"R{number:02d}" for number in range(1, 7))
    first_check = runner.invoke(app, ["check", "--regras", selected])
    assert first_check.exit_code == 0, first_check.exception or first_check.output
    run_id = first_check.stdout.splitlines()[0].removeprefix("run_id: ")
    with connect_database(tmp_path / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT rule_id, id_documento, amount FROM findings ORDER BY rule_id"
        ).fetchall() == [
            (f"R{number:02d}", 209900002 + (number - 1) * 6, Decimal("0.01"))
            for number in range(1, 7)
        ]
        original = db.execute("SELECT * FROM findings ORDER BY rule_id").fetchall()
        assert db.execute(
            "SELECT count(*) FROM findings f JOIN despesas_current d "
            "ON f.id_documento=d.id_documento AND f.source_file_id=d.source_file_id "
            "AND f.record_sha256=d.record_sha256 "
            "WHERE f.run_id=? AND f.status='active'",
            [run_id],
        ).fetchone() == (6,)
        for rule_id, encoded in db.execute(
            "SELECT rule_id, evidence_json FROM findings ORDER BY rule_id"
        ).fetchall():
            evidence = json.loads(encoded)
            number = int(rule_id[1:])
            document = 209900002 + (number - 1) * 6
            assert evidence["documentos"] == [document - 1, document]
            assert Decimal(evidence["total"]) - Decimal(evidence["limite"]) == Decimal(
                "0.01"
            )
    forced = runner.invoke(app, ["ingest", "--force"])
    assert forced.exit_code == 0, forced.exception or forced.output
    second_check = runner.invoke(app, ["check", "--regras", selected])
    assert second_check.exit_code == 0, second_check.exception or second_check.output
    with connect_database(tmp_path / "db.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM findings ORDER BY rule_id").fetchall() == original
        )
        assert db.execute("SELECT count(*) FROM despesas").fetchone() == (400,)
        assert db.execute("SELECT count(*) FROM despesas_current").fetchone() == (200,)

"""The complete Phase 2 rule selection detects the fixture's intended cases."""

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


def test_all_phase_two_rules_have_exact_expected_findings_and_stable_retries(
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
    ingestion = runner.invoke(app, ["ingest"])
    assert ingestion.exit_code == 0, ingestion.exception or ingestion.output
    selected = ",".join([*(f"R{number:02d}" for number in range(1, 11)), "R14"])
    checked = runner.invoke(app, ["check", "--regras", selected])
    assert checked.exit_code == 0, checked.exception or checked.output
    expected = [
        (f"R{number:02d}", 209900002 + (number - 1) * 6) for number in range(1, 7)
    ] + [
        ("R07", 209900032),
        ("R07", 209900034),
        ("R07", 209900036),
        ("R07", 209900055),
        ("R08", 209900040),
        ("R09", 209900045),
        ("R09", 209900046),
        ("R10", 209900048),
        ("R14", 209900037),
    ]
    with connect_database(tmp_path / "db.duckdb", read_only=True) as db:
        assert (
            db.execute(
                "SELECT rule_id, id_documento FROM findings "
                "ORDER BY rule_id,id_documento"
            ).fetchall()
            == expected
        )
        original = db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
        assert db.execute(
            "SELECT rule_id, amount FROM findings WHERE rule_id IN ('R08','R09','R10') "
            "ORDER BY rule_id,id_documento"
        ).fetchall() == [
            ("R08", Decimal("25.00")),
            ("R09", Decimal("321.09")),
            ("R09", Decimal("321.09")),
            ("R10", Decimal("10.00")),
        ]
    run_id = checked.stdout.splitlines()[0].removeprefix("run_id: ")
    manifest = json.loads((tmp_path / "runs" / run_id / "manifest.json").read_text())
    assert manifest["counters"]["rules_completed"] == 11
    assert manifest["counters"]["findings_detected"] == 15
    second = runner.invoke(app, ["check", "--regras", selected])
    assert second.exit_code == 0, second.exception or second.output
    with connect_database(tmp_path / "db.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
            == original
        )

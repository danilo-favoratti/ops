"""Annual quotas honor margins while unsupported pooled budgets stay suspended."""

import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


@pytest.mark.parametrize("margin", ["5", "0"])
def test_quota_margin_and_leadership_on_synthetic_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    margin: str,
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
    monkeypatch.setenv("OPSIU_RULES__QUOTA_MARGIN_PERCENT", margin)
    runner = CliRunner()
    ingestion = runner.invoke(app, ["ingest"])
    assert ingestion.exit_code == 0, ingestion.exception or ingestion.output
    result = runner.invoke(app, ["check", "--regras", "R07,R14"])
    assert result.exit_code == 0, result.exception or result.output
    expected = [
        ("R07", 209900032),
        ("R07", 209900034),
        ("R07", 209900036),
        ("R07", 209900055),
    ]
    if margin == "0":
        expected.extend([("R07", 209900053), ("R07", 209900054)])
    with connect_database(tmp_path / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT rule_id, id_documento FROM findings ORDER BY rule_id, id_documento"
        ).fetchall() == sorted(expected)
        # The high February spend by deputy 501 is covered by unused January quota.
        assert db.execute(
            "SELECT count(*) FROM findings WHERE id_documento=209900052"
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM findings WHERE rule_id='R14'"
        ).fetchone() == (0,)
    run_id = result.stdout.splitlines()[0].removeprefix("run_id: ")
    manifest = json.loads((tmp_path / "runs" / run_id / "manifest.json").read_text())
    assert manifest["counters"]["findings_detected"] == len(expected)
    assert manifest["counters"]["rules_suspended"] == 1
    assert manifest["counters"]["rules_skipped"] == 1
    assert manifest["counters"]["rules_completed"] == 1
    assert set(manifest["rule_versions"]) == {"R07", "R14"}

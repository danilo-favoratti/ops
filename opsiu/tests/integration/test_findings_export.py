"""Exports retain original finding provenance and never replace earlier artifacts."""

import csv
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("synthetic_cap_policies"),
]
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"
LOCAL_RULES = ",".join([*(f"R{number:02d}" for number in range(1, 11)), "R14"])


def invoke(*arguments: str) -> Result:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == 0, result.exception or result.output
    return result


def run_id(result: Result) -> str:
    return result.stdout.splitlines()[0].removeprefix("run_id: ")


def test_findings_export_keeps_snapshot_provenance_and_previous_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    source = tmp_path / "input/Ano-2099.json"
    shutil.copyfile(FIXTURE, source)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        "[years]\nfrom=2099\nto=2099\n",
        encoding="utf-8",
    )
    invoke("ingest")
    checked = invoke("check", "--regras", LOCAL_RULES)
    # A later source snapshot must not relabel findings' original provenance.
    invoke("ingest", "--force")
    exported = invoke("report", "findings")
    directory = tmp_path / "runs" / run_id(exported)
    csv_file = directory / "findings.csv"
    markdown = directory / "findings.md"
    with csv_file.open(encoding="utf-8-sig", newline="") as stream:
        records = list(csv.DictReader(stream))
    assert len(records) == 13
    assert {record["source_sha256"] for record in records} == {source_sha}
    assert {record["run_id"] for record in records} == {run_id(checked)}
    assert {record["status"] for record in records} == {"active"}
    assert records[0]["rule_id"] == "R01"
    assert records[0]["amount"] == "0.01"
    assert json.loads(records[0]["evidence_json"])["documentos"] == [
        209900001,
        209900002,
    ]
    with connect_database(tmp_path / "db.duckdb", read_only=True) as db:
        row = db.execute(
            "SELECT source_file_id FROM source_files ORDER BY ingested_at LIMIT 1"
        ).fetchone()
        assert row is not None
        original_source = str(row[0])
    assert {record["source_file_id"] for record in records} == {original_source}
    text = markdown.read_text(encoding="utf-8")
    assert all(
        rule in text for rule in LOCAL_RULES.split(",") if rule not in {"R10", "R14"}
    )
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert "findings.csv" in str(manifest["artifacts"])
    assert "findings.md" in str(manifest["artifacts"])
    artifacts = {
        Path(artifact["path"]).name: artifact for artifact in manifest["artifacts"]
    }
    for artifact_file in (csv_file, markdown):
        artifact = artifacts[artifact_file.name]
        assert (
            artifact["sha256"] == hashlib.sha256(artifact_file.read_bytes()).hexdigest()
        )
        assert artifact["size_bytes"] == artifact_file.stat().st_size
    original_csv, original_markdown = csv_file.read_bytes(), markdown.read_bytes()

    source.write_text('{"dados": []}', encoding="utf-8")
    invoke("ingest")
    invoke("check", "--regras", LOCAL_RULES)
    empty = invoke("report", "findings")
    assert run_id(empty) != run_id(exported)
    with (tmp_path / "runs" / run_id(empty) / "findings.csv").open(
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        assert list(csv.DictReader(stream)) == []
    assert csv_file.read_bytes() == original_csv
    assert markdown.read_bytes() == original_markdown

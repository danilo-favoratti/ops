"""Graph HTTP reads are bounded, audited and independent of audit findings."""

import json
import os
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app as cli
from opsiu.interfaces.web.app import create_app
from opsiu.settings import Settings

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    shutil.copyfile(FIXTURE, tmp_path / "input/Ano-2099.json")
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2099\nto=2099\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(cli, ["ingest"])
    assert result.exit_code == 0, result.exception or result.output
    return tmp_path


def test_graph_shell_and_assets_use_existing_security(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        response = client.get("/graph")
        assert response.status_code == 200
        assert "Mapa dos reembolsos" in response.text
        assert 'id="graph-node-budget"' in response.text
        assert "Diagnóstico e controles" in response.text
        assert "unsafe-eval" not in response.headers["Content-Security-Policy"]
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-OPSIU-Run-ID"] in response.text
        for filename in ("pixi.min.js", "graph.js", "graph_state.js", "graph.css"):
            asset = client.get("/static/" + filename)
            assert asset.status_code == 200
            assert len(asset.content) > 100
        assert client.get("/static/package-lock.json").status_code == 404
        assert 'href="/graph"' in client.get("/findings").text


def test_graph_pages_cover_all_accounting_rows_without_findings(
    workspace: Path,
) -> None:
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        expected = db.execute(
            "SELECT count(*), sum(valor_liquido) FROM despesas_current"
        ).fetchone()
        assert db.execute("SELECT count(*) FROM findings").fetchone() == (0,)
    assert expected is not None
    ids: set[str] = set()
    cursor = ""
    snapshots: set[str] = set()
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        for _ in range(6):
            response = client.get("/api/graph", params={"limit": 40, "cursor": cursor})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["summary"]["reimbursements"] == expected[0]
            assert Decimal(data["summary"]["total_amount"]) == expected[1]
            assert data["page"]["returned"] <= 40
            assert len(data["nodes"]) <= 120
            assert len(data["edges"]) == data["page"]["returned"] * 2
            node_ids = {node["id"] for node in data["nodes"]}
            receipts = {
                node["id"] for node in data["nodes"] if node["kind"] == "reimbursement"
            }
            assert not ids.intersection(receipts)
            ids.update(receipts)
            for edge in data["edges"]:
                assert edge["source"] in node_ids and edge["target"] in node_ids
            snapshots.add(data["snapshot"])
            run_id = response.headers["X-OPSIU-Run-ID"]
            manifest = json.loads(
                (workspace / "runs" / run_id / "manifest.json").read_text()
            )
            assert manifest["status"] == "completed"
            assert manifest["command"] == "graph.data"
            assert manifest["counters"]["graph_records_returned"] == len(receipts)
            if not data["page"]["has_more"]:
                assert data["page"]["next_cursor"] is None
                break
            cursor = data["page"]["next_cursor"]
    assert len(ids) == expected[0] == 200
    assert len(snapshots) == 1
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM despesas_current").fetchone() == (200,)
        assert db.execute("SELECT count(*) FROM findings").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM decisions").fetchone() == (0,)


def test_graph_search_and_filtered_neighborhood(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        for kind in ("parliamentarian", "supplier"):
            response = client.get(
                "/api/graph/search", params={"kind": kind, "year": 2099, "limit": 3}
            )
            assert response.status_code == 200
            data = response.json()
            assert 1 <= len(data["items"]) <= 3
            item = data["items"][0]
            assert item["label"] and item["kind"] == kind
            parameter = "deputy_id" if kind == "parliamentarian" else "supplier_id"
            identity = (
                item["id"].split(":")[1] if parameter == "deputy_id" else item["id"]
            )
            page = client.get(
                "/api/graph", params={parameter: identity, "year": 2099}
            ).json()
            entities = [node for node in page["nodes"] if node["kind"] == kind]
            assert [node["id"] for node in entities] == [item["id"]]
        empty = client.get("/api/graph", params={"year": 2020}).json()
        assert empty["nodes"] == empty["edges"] == []
        assert empty["summary"]["reimbursements"] == 0
        assert empty["page"]["has_more"] is False
        absent = client.get(
            "/api/graph/search", params={"kind": "supplier", "q": "absent!' OR 1=1"}
        )
        assert absent.status_code == 200 and absent.json()["items"] == []


@pytest.mark.parametrize(
    "url",
    [
        "/api/graph?limit=101",
        "/api/graph?limit=0",
        "/api/graph?year=not-a-year",
        "/api/graph?deputy_id=9223372036854775808",
        "/api/graph?limit=20&limit=40",
        "/api/graph?unexpected=value",
        "/api/graph?query=" + "a" * 121,
        "/api/graph?cursor=not-a-valid-cursor",
        "/api/graph/search?kind=unknown",
        "/api/graph/search?kind=supplier&limit=21",
    ],
)
def test_graph_errors_are_json_and_leave_failed_runs(workspace: Path, url: str) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        response = client.get(url)
        assert response.status_code == 400
        assert response.headers["Content-Type"] == "application/json"
        run_id = response.headers["X-OPSIU-Run-ID"]
        assert response.json()["run_id"] == run_id
        manifest = json.loads(
            (workspace / "runs" / run_id / "manifest.json").read_text()
        )
        assert manifest["status"] == "completed_with_errors"
        assert manifest["steps"][0]["status"] == "failed"
        assert manifest["command"].startswith("graph.")


def test_graph_rejects_remote_host_and_stale_snapshot(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        rejected = client.get("/api/graph", headers={"Host": "external.example"})
        assert rejected.status_code == 400
        assert rejected.json()["run_id"]
        first = client.get("/api/graph", params={"limit": 20}).json()
        source = workspace / "input/Ano-2099.json"
        data = json.loads(source.read_text())
        data["dados"][0]["fornecedor"] = "Novo fornecedor sintético"
        source.write_text(json.dumps(data), encoding="utf-8")
        result = CliRunner().invoke(cli, ["ingest"])
        assert result.exit_code == 0, result.exception or result.output
        stale = client.get(
            "/api/graph", params={"limit": 20, "cursor": first["page"]["next_cursor"]}
        )
        assert stale.status_code == 409
        assert "Recomece" in stale.json()["error"]

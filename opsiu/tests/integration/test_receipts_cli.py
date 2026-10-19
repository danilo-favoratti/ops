"""Bounded receipt groups retain source provenance and verified cached bytes."""

import io
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pypdf import PdfWriter
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
BASE_EXPENSE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


@dataclass
class ReceiptServer:
    url: str = ""
    requests: list[str] = field(default_factory=list)
    invalid_paths: set[str] = field(default_factory=lambda: {"/invalid"})


@pytest.fixture
def receipt_server() -> Iterator[ReceiptServer]:
    state = ReceiptServer()
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    output = io.BytesIO()
    writer.write(output)
    receipt = output.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests.append(self.path)
            payload = b"not a PDF" if self.path in state.invalid_paths else receipt
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2099\nto=2099\n',
        encoding="utf-8",
    )
    return tmp_path


def expense(document: int, url: str, **changes: object) -> dict[str, object]:
    base: dict[str, object] = json.loads(BASE_EXPENSE.read_text())["dados"][0]
    base.update(
        idDocumento=str(document),
        numeroSubCota="3",
        cnpjCPF="00000030355",
        numero="DUP-123",
        dataEmissao="2099-01-01",
        mes=1,
        valorDocumento="30.00",
        valorLiquido="30.00",
        valorGlosa="0.00",
        urlDocumento=url,
    )
    return base | changes


def ingest(root: Path, data: list[dict[str, object]]) -> None:
    (root / "input/Ano-2099.json").write_text(json.dumps({"dados": data}))
    invoke("ingest")


def invoke(*arguments: str, expected: int = 0) -> str:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.output or result.exception
    return result.stdout.splitlines()[0].removeprefix("run_id: ")


def test_complete_groups_respect_limit_priority_urls_provenance_and_cache(
    workspace: Path, receipt_server: ReceiptServer
) -> None:
    url = receipt_server.url
    data = [expense(index, f"{url}/{index}") for index in (1, 2)]
    data += [
        expense(index, f"{url}/{index}", numero="LARGER", valorDocumento="50.00")
        for index in (3, 4, 5)
    ]
    data += [
        expense(index, f"{url}/{index}", numero="s/n", valorDocumento="1000.00")
        for index in (6, 7)
    ]
    data += [
        expense(8, f"{url}/8", numero="MISSING", valorDocumento="100.00"),
        expense(9, "", numero="MISSING", valorDocumento="100.00"),
        expense(10, f"{url}/10", numero="AMBIGUOUS", valorDocumento="200.00"),
        expense(11, f"{url}/11", numero="AMBIGUOUS", valorDocumento="200.00"),
        expense(11, f"{url}/11-other", numero="AMBIGUOUS", valorDocumento="200.00"),
    ]
    ingest(workspace, data)
    invoke("check", "--regras", "R09")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        findings_before = db.execute(
            "SELECT * FROM findings ORDER BY finding_key"
        ).fetchall()
    run = invoke("receipts", "fetch", "--limit", "2")
    invoke("check", "--regras", "R09")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
            == findings_before
        )
    assert receipt_server.requests == ["/1", "/2"]
    manifest = json.loads((workspace / "runs" / run / "manifest.json").read_text())
    assert manifest["counters"]["receipt_groups_selected"] == 1
    assert manifest["counters"]["receipt_groups_over_limit"] == 2
    assert manifest["counters"]["receipt_groups_ambiguous"] == 1
    assert manifest["counters"]["receipt_groups_missing_url"] == 1
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        original = db.execute("SELECT * FROM nf_texto ORDER BY id_documento").fetchall()
        assert len(original) == 2
        assert db.execute("SELECT count(*) FROM nf_texto_findings").fetchone() == (2,)
        events = db.execute(
            "SELECT attributes_json FROM events WHERE run_id=? "
            "AND kind='receipt.extracted' ORDER BY created_at",
            [run],
        ).fetchall()
        assert len(events) == 2
        assert all(json.loads(row[0])["sources"][0]["record_sha256"] for row in events)
    replay = invoke("receipts", "fetch", "--limit", "2")
    assert receipt_server.requests == ["/1", "/2"]
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM nf_texto ORDER BY id_documento").fetchall()
            == original
        )
    replay_manifest = json.loads(
        (workspace / "runs" / replay / "manifest.json").read_text()
    )
    assert replay_manifest["counters"]["receipts_cached"] == 2


def test_identical_receipts_reveal_placeholder_duplicates_and_replay_is_stable(
    workspace: Path, receipt_server: ReceiptServer
) -> None:
    receipt_server.invalid_paths.add("/3")
    ingest(
        workspace,
        [
            expense(1, receipt_server.url + "/1", numero="s/n"),
            expense(1, receipt_server.url + "/1", numero="s/n", valorLiquido="10.00"),
            expense(
                2, receipt_server.url + "/2", numero="s/n", dataEmissao="2099-01-02"
            ),
            expense(
                3, receipt_server.url + "/3", numero="s/n", dataEmissao="2099-01-03"
            ),
        ],
    )
    invoke("check", "--regras", "R09")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM findings").fetchone() == (0,)
    invoke("receipts", "fetch", "--limit", "3", expected=1)
    invoke("check", "--regras", "R09")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        original = db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
        assert len(original) == 2
        evidence = db.execute("SELECT evidence_json FROM findings").fetchall()
        proofs = [json.loads(row[0]) for row in evidence]
        assert any("ocorrencias" in proof for proof in proofs)
        assert all(
            proof.get("criterio") == "comprovantes_identicos_byte_a_byte"
            or proof["ocorrencias"][0]["evidencia"]["criterio"]
            == "comprovantes_identicos_byte_a_byte"
            for proof in proofs
        )
    invoke("check", "--regras", "R09")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
            == original
        )
    receipt_server.invalid_paths.remove("/3")
    invoke("receipts", "fetch", "--limit", "3")
    invoke("check", "--regras", "R09")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        expanded = db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
        assert len(expanded) == 3
        assert all(row in expanded for row in original)
    original = expanded
    cached = workspace / "external/pdf/1.pdf"
    cached.write_bytes(b"tampered")
    invoke("check", "--regras", "R09", expected=1)
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert (
            db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
            == original
        )
    assert cached.read_bytes() == b"tampered"
    assert receipt_server.requests == ["/1", "/2", "/3", "/3"]


def test_failed_receipt_does_not_stop_other_group_members(
    workspace: Path, receipt_server: ReceiptServer
) -> None:
    ingest(
        workspace,
        [
            expense(1, receipt_server.url + "/invalid"),
            expense(2, receipt_server.url + "/2"),
        ],
    )
    run = invoke("receipts", "fetch", "--limit", "2", expected=1)
    manifest = json.loads((workspace / "runs" / run / "manifest.json").read_text())
    assert manifest["counters"]["receipts_failed"] == 1
    assert manifest["counters"]["receipts_extracted"] == 1
    assert receipt_server.requests == ["/invalid", "/2"]
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT id_documento FROM nf_texto").fetchall() == [(2,)]


@pytest.mark.parametrize("limit", [1, 201])
def test_invalid_limits_are_recorded_without_opening_database(
    workspace: Path, limit: int
) -> None:
    run = invoke("receipts", "fetch", "--limit", str(limit), expected=2)
    assert not (workspace / "db.duckdb").exists()
    assert (workspace / "runs" / run / "manifest.json").exists()


def test_uncached_only_advances_groups_and_keeps_cached_members_within_limit(
    workspace: Path, receipt_server: ReceiptServer
) -> None:
    data = [expense(index, f"{receipt_server.url}/{index}") for index in (1, 2)]
    data += [
        expense(
            index,
            f"{receipt_server.url}/{index}",
            numero="LOWER",
            valorDocumento="20.00",
        )
        for index in (3, 4)
    ]
    ingest(workspace, data)
    invoke("receipts", "fetch", "--limit", "2", "--uncached-only")
    assert receipt_server.requests == ["/1", "/2"]
    second = invoke("receipts", "fetch", "--limit", "2", "--uncached-only")
    assert receipt_server.requests == ["/1", "/2", "/3", "/4"]
    manifest = json.loads((workspace / "runs" / second / "manifest.json").read_text())
    assert manifest["counters"]["receipt_groups_cached"] == 1
    assert manifest["counters"]["receipts_selected"] == 2
    third = invoke("receipts", "fetch", "--limit", "2", "--uncached-only")
    manifest = json.loads((workspace / "runs" / third / "manifest.json").read_text())
    assert manifest["counters"]["receipt_groups_cached"] == 2
    assert manifest["counters"]["receipts_selected"] == 0
    data.append(expense(5, receipt_server.url + "/5"))
    ingest(workspace, data)
    too_small = invoke("receipts", "fetch", "--limit", "2", "--uncached-only")
    manifest = json.loads(
        (workspace / "runs" / too_small / "manifest.json").read_text()
    )
    assert manifest["counters"]["receipt_groups_over_limit"] == 1
    assert manifest["counters"]["receipts_selected"] == 0
    fourth = invoke("receipts", "fetch", "--limit", "3", "--uncached-only")
    manifest = json.loads((workspace / "runs" / fourth / "manifest.json").read_text())
    assert manifest["counters"]["receipts_selected"] == 3
    assert manifest["counters"]["receipts_cached"] == 2
    assert manifest["counters"]["receipts_extracted"] == 1
    assert receipt_server.requests == ["/1", "/2", "/3", "/4", "/5"]

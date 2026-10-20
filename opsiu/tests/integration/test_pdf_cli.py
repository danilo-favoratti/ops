"""Sampled invoice PDFs retain immutable bytes and survive check retries."""

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
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
BASE_EXPENSE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def generated_pdf(text: str | None) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text is not None:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        contents = DecodedStreamObject()
        contents.set_data(f"BT /F1 12 Tf 20 250 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = contents
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@dataclass
class PdfServer:
    url: str = ""
    text_pdf: bytes = field(
        default_factory=lambda: generated_pdf("Nota fiscal sintetica: R$ 25,00")
    )
    blank_pdf: bytes = field(default_factory=lambda: generated_pdf(None))
    requests: list[str] = field(default_factory=list)


@pytest.fixture
def pdf_server() -> Iterator[PdfServer]:
    state = PdfServer()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests.append(self.path)
            payload = state.blank_pdf if self.path == "/blank" else state.text_pdf
            if self.path == "/invalid":
                payload = b"this is not a PDF"
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
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pdf_server: PdfServer
) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2099\nto=2099\n'
        "[sampling]\nper_rule=4\n",
        encoding="utf-8",
    )
    base: dict[str, object] = json.loads(BASE_EXPENSE.read_text())["dados"][0]
    rows = [
        dict(
            base,
            idDocumento=str(209920001 + index),
            numeroSubCota="3",
            cnpjCPF="00000030355",
            numero="PDF-DUP",
            dataEmissao="2099-01-01",
            mes=1,
            valorDocumento="30.00",
            valorLiquido=amount,
            valorGlosa="0.00" if index == 0 else "10.00",
            urlDocumento=pdf_server.url + route,
        )
        for index, (amount, route) in enumerate(
            (("30.00", "/text"), ("20.00", "/blank"))
        )
    ]
    (tmp_path / "input/Ano-2099.json").write_text(
        json.dumps({"dados": rows}), encoding="utf-8"
    )
    invoke("ingest")
    invoke("check", "--regras", "R08")
    invoke("check", "--regras", "R08")
    return tmp_path


def invoke(*arguments: str, expected: int = 0) -> str:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.output or result.exception
    return result.stdout.splitlines()[0].removeprefix("run_id: ")


def rows(root: Path) -> list[tuple[object, ...]]:
    with connect_database(root / "db.duckdb", read_only=True) as db:
        return db.execute("SELECT * FROM nf_texto ORDER BY id_documento").fetchall()


def test_latest_retry_and_dry_run_sampling_use_active_findings_and_verified_cache(
    workspace: Path,
    pdf_server: PdfServer,
) -> None:
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        latest = db.execute(
            "SELECT run_id FROM runs WHERE command='check' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        original = db.execute("SELECT DISTINCT run_id FROM findings").fetchone()
        assert latest != original
    invoke("fetch", "pdf", "--sample")
    assert pdf_server.requests == ["/text", "/blank"]
    original_rows = rows(workspace)
    assert len(original_rows) == 2
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT id_documento,needs_ocr FROM nf_texto ORDER BY id_documento"
        ).fetchall() == [
            (209920001, False),
            (209920002, True),
        ]
        texts = db.execute(
            "SELECT texto,extractor,extractor_version FROM nf_texto "
            "ORDER BY id_documento"
        ).fetchall()
        assert "Nota fiscal sintetica" in texts[0][0]
        assert texts[1][0].strip() == ""
        assert all(row[1] == "pypdf" and row[2] for row in texts)
        original_links = db.execute(
            "SELECT * FROM nf_texto_findings ORDER BY finding_key"
        ).fetchall()
        assert len(original_links) == 2
    invoke("fetch", "pdf", "--sample")
    invoke("check", "--regras", "R08", "--dry-run")
    invoke("fetch", "pdf", "--sample")
    assert len(pdf_server.requests) == 2
    assert rows(workspace) == original_rows
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert (
            db.execute(
                "SELECT * FROM nf_texto_findings ORDER BY finding_key"
            ).fetchall()
            == original_links
        )
    first_path = workspace / "external/pdf/209920001.pdf"
    original_bytes = first_path.read_bytes()
    pdf_server.text_pdf = generated_pdf("Nota fiscal revisada: R$ 30,00")
    invoke("fetch", "pdf", "--sample", "--refresh")
    assert len(pdf_server.requests) == 4
    assert first_path.read_bytes() == original_bytes
    refreshed = rows(workspace)
    assert len(refreshed) > len(original_rows)
    assert all(row in refreshed for row in original_rows)


def test_invalid_pdf_and_missing_url_do_not_prevent_other_sampled_extractions(
    workspace: Path,
    pdf_server: PdfServer,
) -> None:
    source = workspace / "input/Ano-2099.json"
    data = json.loads(source.read_text())
    base = data["dados"][0]
    data["dados"].extend(
        [
            dict(
                base,
                idDocumento="209920003",
                numero="PDF-invalid",
                valorDocumento="35.00",
                valorLiquido="35.00",
                urlDocumento=pdf_server.url + "/invalid",
            ),
            dict(
                base,
                idDocumento="209920004",
                numero="PDF-missing",
                valorDocumento="40.00",
                valorLiquido="40.00",
                urlDocumento="",
            ),
        ]
    )
    source.write_text(json.dumps(data), encoding="utf-8")
    invoke("ingest")
    invoke("check", "--regras", "R08")
    invoke("fetch", "pdf", "--sample", expected=1)
    assert set(pdf_server.requests) == {"/invalid", "/text", "/blank"}
    assert len(rows(workspace)) == 2


def test_latest_successful_rule_selection_does_not_sample_older_unselected_rules(
    workspace: Path,
    pdf_server: PdfServer,
) -> None:
    invoke("check", "--regras", "R02")
    invoke("fetch", "pdf", "--sample")
    assert pdf_server.requests == []
    assert rows(workspace) == []


def test_modified_cached_pdf_is_rejected_without_overwriting_or_downloading(
    workspace: Path,
    pdf_server: PdfServer,
) -> None:
    invoke("fetch", "pdf", "--sample")
    original = rows(workspace)
    cached = workspace / "external/pdf/209920001.pdf"
    cached.write_bytes(b"tampered")
    invoke("fetch", "pdf", "--sample", expected=1)
    assert len(pdf_server.requests) == 2
    assert cached.read_bytes() == b"tampered"
    assert rows(workspace) == original


def test_one_pdf_links_to_multiple_sampled_rules_without_duplicate_downloads(
    workspace: Path,
    pdf_server: PdfServer,
) -> None:
    invoke("check", "--regras", "R08,R09")
    invoke("fetch", "pdf", "--sample")
    assert pdf_server.requests == ["/text", "/blank"]
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM nf_texto").fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM nf_texto_findings").fetchone() == (4,)
        assert db.execute(
            "SELECT count(*) FROM findings f LEFT JOIN nf_texto_findings n "
            "USING(finding_key) WHERE n.finding_key IS NULL"
        ).fetchone() == (0,)


@pytest.mark.parametrize("changed_url", [False, True])
def test_deleted_historical_cache_path_is_never_reused_for_new_bytes(
    workspace: Path,
    pdf_server: PdfServer,
    changed_url: bool,
) -> None:
    invoke("fetch", "pdf", "--sample")
    original = rows(workspace)
    initial = workspace / "external/pdf/209920001.pdf"
    initial.unlink()
    pdf_server.text_pdf = generated_pdf("Nota fiscal revisada: R$ 30,00")
    if changed_url:
        source = workspace / "input/Ano-2099.json"
        data = json.loads(source.read_text())
        data["dados"][0]["urlDocumento"] = pdf_server.url + "/new"
        source.write_text(json.dumps(data), encoding="utf-8")
        invoke("ingest")
        # R09 is a new finding versioned independently from the existing R08 rows.
        invoke("check", "--regras", "R09")
        invoke("fetch", "pdf", "--sample")
    else:
        invoke("fetch", "pdf", "--sample", "--refresh")
    assert not initial.exists()
    current = rows(workspace)
    assert all(row in current for row in original)
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        new_paths = db.execute(
            "SELECT path FROM nf_texto WHERE id_documento=209920001 "
            "AND texto LIKE '%revisada%'"
        ).fetchall()
        assert len(new_paths) == 1
        assert (
            Path(new_paths[0][0]).parent
            == workspace / "external/pdf/versions/209920001"
        )
        assert Path(new_paths[0][0]).read_bytes() == pdf_server.text_pdf

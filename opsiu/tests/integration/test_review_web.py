"""Exercise the local review interface against real runs and immutable history."""

import io
import json
import os
import threading
from collections.abc import Iterator
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.domain.findings import finding_key
from opsiu.interfaces.cli import app as cli
from opsiu.interfaces.web.app import create_app
from opsiu.settings import Settings

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"
KEY = finding_key("R08", "1", 209900040)
DUPLICATE = finding_key("R09", "2", 209900045)
CAP = finding_key("R01", "2", 209900002)


class Page(HTMLParser):
    def __init__(self, document: str) -> None:
        super().__init__()
        self.token: str | None = None
        self.scripts: list[str] = []
        self.feed(document)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")
        source = attributes.get("src")
        if tag == "script" and source is not None:
            self.scripts.append(source)


@pytest.fixture
def pdf_server() -> Iterator[tuple[str, bytes]]:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    contents = DecodedStreamObject()
    contents.set_data(b"BT /F1 12 Tf 20 250 Td (Nota fiscal sintetica WEB) Tj ET")
    page[NameObject("/Contents")] = contents
    buffer = io.BytesIO()
    writer.write(buffer)
    payload = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/invoice.pdf", payload
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def invoke(*arguments: str) -> None:
    result = CliRunner().invoke(cli, list(arguments))
    assert result.exit_code == 0, result.exception or result.output


@pytest.fixture
def workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    pdf_server: tuple[str, bytes],
) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["dados"][39].update(
        nomeParlamentar="Deputada <script>alert(1)</script>",
        fornecedor='Fornecedor <img src=x onerror="alert(2)">',
        urlDocumento=getattr(request, "param", pdf_server[0]),
    )
    (tmp_path / "input/Ano-2099.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2099\nto=2099\n'
        '[review]\nreviewer="Revisora web"\n',
        encoding="utf-8",
    )
    invoke("ingest")
    invoke("check", "--regras", "R01,R02,R08,R09,R10")
    invoke("explain", "--regras", "R08")
    return tmp_path


def counts(workspace: Path) -> tuple[int, int]:
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        runs = db.execute("SELECT count(*) FROM runs").fetchone()
        decisions = db.execute("SELECT count(*) FROM decisions").fetchone()
        assert runs is not None and decisions is not None
        return int(runs[0]), int(decisions[0])


def test_list_filters_detail_escape_and_local_htmx(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        before = counts(workspace)
        response = client.get("/")
        assert response.status_code == 200
        assert "OPSIU" in response.text and KEY in response.text
        assert counts(workspace) == (before[0] + 1, before[1])
        assert response.headers["X-OPSIU-Run-ID"] in response.text
        scripts = Page(response.text).scripts
        assert scripts and all(
            not url.startswith(("http:", "https:")) for url in scripts
        )
        for source in scripts:
            asset = client.get(source)
            assert asset.status_code == 200
            if source.endswith("htmx.min.js"):
                assert "htmx" in asset.text.lower()
            else:
                assert source == "/static/filters.js"
                assert "data-deputy-picker" in asset.text
        filtered = client.get(
            "/findings", params={"rule": "R08", "deputy": 201, "uf": "SP"}
        )
        assert filtered.status_code == 200 and KEY in filtered.text
        assert DUPLICATE not in filtered.text and CAP not in filtered.text
        minimum = client.get("/findings", params={"min_amount": "25.01"})
        assert DUPLICATE in minimum.text and KEY not in minimum.text
        detail = client.get(f"/findings/{KEY}")
        assert detail.status_code == 200
        assert detail.text.index('id="basis-heading"') < detail.text.index(
            'class="breadcrumb"'
        )
        assert "Recomendação do agente" in detail.text
        assert "Dados completos do parecer (JSON)" in detail.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in detail.text
        assert "<script>alert(1)</script>" not in detail.text
        assert "<img src=x" not in detail.text
        assert "scripted" in detail.text
        assert Page(detail.text).token


def test_decision_forms_append_one_run_each_and_return_htmx_partial(
    workspace: Path,
) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        detail = client.get(f"/findings/{KEY}")
        token = Page(detail.text).token
        assert token
        before = counts(workspace)
        approved = client.post(
            f"/findings/{KEY}/decisions",
            data={
                "decision": "aprovado",
                "comment": "Conferido <manualmente>.",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert approved.status_code == 303
        assert counts(workspace) == (before[0] + 1, before[1] + 1)
        rejected = client.post(
            f"/findings/{KEY}/decisions",
            data={
                "decision": "rejeitado",
                "comment": "Revisão posterior.",
                "csrf_token": token,
            },
            headers={"HX-Request": "true"},
        )
        assert rejected.status_code == 200
        assert "<html" not in rejected.text.lower()
        assert "rejeitad" in rejected.text.lower()
        assert counts(workspace) == (before[0] + 2, before[1] + 2)
        with connect_database(workspace / "db.duckdb", read_only=True) as db:
            assert db.execute(
                "SELECT decision, reviewer, comment FROM decisions "
                "ORDER BY decided_at, decision_id"
            ).fetchall() == [
                ("aprovado", "Revisora web", "Conferido <manualmente>."),
                ("rejeitado", "Revisora web", "Revisão posterior."),
            ]
            for response in (approved, rejected):
                identity = response.headers["X-OPSIU-Run-ID"]
                assert db.execute(
                    "SELECT status FROM steps WHERE run_id=?", [identity]
                ).fetchall() == [("completed",)]
                assert db.execute(
                    "SELECT CAST(run_id AS VARCHAR) FROM decisions WHERE run_id=?",
                    [identity],
                ).fetchall() == [(identity,)]
        listed = client.get("/findings", params={"rule": "R08"})
        assert KEY not in listed.text


@pytest.mark.parametrize("attack", ["missing", "tampered", "foreign_origin"])
def test_forged_posts_fail_inside_audited_run(workspace: Path, attack: str) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        token = Page(client.get(f"/findings/{KEY}").text).token
        assert token
        form = {"decision": "aprovado", "csrf_token": token}
        headers: dict[str, str] = {}
        if attack == "missing":
            form.pop("csrf_token")
        elif attack == "tampered":
            form["csrf_token"] += "changed"
        else:
            headers["Origin"] = "https://untrusted.example"
        before = counts(workspace)
        response = client.post(f"/findings/{KEY}/decisions", data=form, headers=headers)
        assert response.status_code == 403
        assert counts(workspace) == (before[0] + 1, before[1])
        with connect_database(workspace / "db.duckdb", read_only=True) as db:
            assert db.execute(
                "SELECT status FROM steps WHERE run_id=?",
                [response.headers["X-OPSIU-Run-ID"]],
            ).fetchall() == [("failed",)]


@pytest.mark.parametrize("workspace", ["javascript:alert(1)"], indirect=True)
def test_unsafe_original_pdf_url_is_not_clickable(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        response = client.get(f"/findings/{KEY}")
        assert response.status_code == 200
        assert 'href="javascript:' not in response.text.lower()
        assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400


def test_pdf_text_download_and_tampered_cache(
    workspace: Path, pdf_server: tuple[str, bytes]
) -> None:
    invoke("check", "--regras", "R08")
    invoke("fetch", "pdf", "--sample")
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        detail = client.get(f"/findings/{KEY}")
        assert detail.status_code == 200 and "Nota fiscal sintetica WEB" in detail.text
        response = client.get(f"/findings/{KEY}/pdf")
        assert response.status_code == 200
        assert response.content == pdf_server[1]
        assert response.headers["content-type"].startswith("application/pdf")
        with connect_database(workspace / "db.duckdb", read_only=True) as db:
            row = db.execute("SELECT path FROM nf_texto").fetchone()
            assert row is not None
        Path(str(row[0])).write_bytes(b"tampered invoice bytes")
        failed = client.get(f"/findings/{KEY}/pdf")
        assert failed.status_code >= 400
        assert b"tampered invoice bytes" not in failed.content


def test_superseded_finding_cannot_be_approved_from_old_form(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        token = Page(client.get(f"/findings/{KEY}").text).token
        assert token
        (workspace / "input/Ano-2099.json").write_text(
            '{"dados": []}', encoding="utf-8"
        )
        invoke("ingest")
        invoke("check", "--regras", "R08")
        response = client.post(
            f"/findings/{KEY}/decisions",
            data={"decision": "aprovado", "csrf_token": token},
        )
        assert response.status_code in (400, 409, 422)
        assert counts(workspace)[1] == 0


def test_numbered_pages_counts_and_filter_preservation(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        params = {"limit": 1, "rule": "R01", "uf": "df", "min_amount": "0,00"}
        first = client.get("/findings", params=params)
        assert first.status_code == 200
        assert "Mostrando 1–1 de" in first.text
        assert 'aria-current="page"' in first.text
        assert 'name="rule" value="R01"' in first.text
        assert 'name="uf" value="DF"' in first.text
        assert 'name="min_amount" value="0,00"' in first.text
        assert 'name="limit" value="1"' in first.text
        assert 'value="0" disabled>← Anterior' in first.text
        second = client.get("/findings", params={"limit": 1, "page": 2})
        assert second.status_code == 200
        assert "Mostrando 2–2 de" in second.text
        assert 'value="1">← Anterior' in second.text
        assert first.text.count('aria-label="Páginas de achados"') == 2
        last = client.get("/findings", params={**params, "page": 999999})
        assert last.status_code == 200
        assert "disabled>Próxima →" in last.text
        assert "Página 999999" not in last.text
        empty = client.get("/findings", params={"deputy": -999, "page": 10})
        assert empty.status_code == 200
        assert "Mostrando 0–0 de 0 achados" in empty.text
        assert 'aria-label="Páginas de achados"' not in empty.text
        invalid = client.get("/findings", params={"page": 0})
        assert invalid.status_code == 400


def test_multiple_deputies_and_states_survive_pagination(workspace: Path) -> None:
    with TestClient(create_app(Settings()), base_url="http://127.0.0.1") as client:
        response = client.get(
            "/findings",
            params=[
                ("deputy", "201"),
                ("deputy", "999999"),
                ("uf", "SP"),
                ("uf", "DF"),
                ("limit", "1"),
            ],
        )
        assert response.status_code == 200
        assert KEY in response.text
        assert 'name="deputy" multiple data-deputy-picker' in response.text
        assert 'value="201" selected' in response.text
        assert 'value="999999" selected' in response.text
        assert 'name="deputy" value="201"' in response.text
        assert 'name="deputy" value="999999"' in response.text
        assert 'name="uf" value="SP" checked' in response.text
        assert 'name="uf" value="DF" checked' in response.text
        assert 'name="uf" value="SP">' in response.text
        assert 'name="uf" value="DF">' in response.text
        assert "<script>alert(1)</script>" not in response.text
        invalid = client.get("/findings", params=[("deputy", "201"), ("deputy", "bad")])
        assert invalid.status_code == 400

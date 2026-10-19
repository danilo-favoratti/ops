"""Offline explanations are sampled, append-only, resumable, and budgeted."""

import io
import json
import os
import threading
from collections.abc import Iterable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from agentrig.agents import AgentExecutionResult
from agentrig.core import JsonValue
from agentrig.testing import ScriptedAgentRuntime, ScriptedAgentScenario
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner, Result

from opsiu.adapters.agents import gateway
from opsiu.adapters.agents.runtimes import build_scripted_runtime
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app
from opsiu.settings import Settings

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def invoice_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
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
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 250 Td (Nota sintetica original: 25,00) Tj ET")
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def invoice_url() -> Iterator[str]:
    payload = invoice_pdf()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "application/pdf")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/nota.pdf"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def invoke(*arguments: str, expected: int = 0) -> Result:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.exception or result.output
    return result


def run_id(result: Result) -> str:
    return result.stdout.splitlines()[0].removeprefix("run_id: ")


@pytest.fixture
def workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invoice_url: str
) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    rows = json.loads(FIXTURE.read_text())["dados"]
    selected = [rows[39], rows[44], rows[45]]
    selected[0]["urlDocumento"] = invoice_url
    (tmp_path / "input/Ano-2099.json").write_text(json.dumps({"dados": selected}))
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2099\nto=2099\n'
        "[agents]\nmax_calls_per_run=4\n",
        encoding="utf-8",
    )
    invoke("ingest")
    invoke("check", "--regras", "R08,R09")
    return tmp_path


def test_offline_sample_retry_force_and_rule_filter_preserve_findings(
    workspace: Path,
) -> None:
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        original = db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()
        assert len(original) == 3
    # A retrying check owns no newly inserted finding rows, but still selects them.
    checked = invoke("check", "--regras", "R08,R09")
    first = invoke("explain", "--amostra", "1")
    assert "simulad" in first.stdout.lower()
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        rows = db.execute(
            "SELECT f.rule_id, p.model, p.kind, p.output_json, p.run_id "
            "FROM pareceres p JOIN findings f USING (finding_key) ORDER BY f.rule_id"
        ).fetchall()
        assert [row[0] for row in rows] == ["R08", "R09"]
        assert all(row[1:3] == ("scripted", "parecer") for row in rows)
        assert all(json.loads(row[3])["recomendacao"] == "revisar" for row in rows)
        assert all(str(row[4]) == run_id(first) for row in rows)
        assert run_id(checked) not in {str(row[8]) for row in original}
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (2,)
    invoke("explain", "--amostra", "1")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (2,)
    invoke("explain", "--amostra", "1", "--force")
    invoke("explain", "--regras", "R08", "--force")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (5,)
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (5,)
        assert db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall() == (
            original
        )


def test_one_call_budget_resumes_invoice_without_repeating_explanation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke("check", "--regras", "R08")
    invoke("fetch", "pdf", "--sample")
    monkeypatch.setenv("OPSIU_AGENTS__MAX_CALLS_PER_RUN", "1")
    first = invoke("explain")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT kind FROM pareceres").fetchall() == [("parecer",)]
        assert db.execute(
            "SELECT count(*) FROM events WHERE run_id=? AND kind='budget.exhausted'",
            [run_id(first)],
        ).fetchone() == (1,)
        trace = db.execute("SELECT input_json FROM agent_traces").fetchone()
        assert trace is not None
        assert "Nota sintetica original" in json.loads(trace[0])["texto_nota"]
        extraction = db.execute(
            "SELECT extraction_id, text_sha256 FROM nf_texto"
        ).fetchone()
        assert extraction is not None
    second = invoke("explain")
    third = invoke("explain")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT kind FROM pareceres ORDER BY kind").fetchall() == [
            ("nota_fiscal",),
            ("parecer",),
        ]
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (2,)
        invoice = db.execute(
            "SELECT p.output_json, t.input_json FROM pareceres p "
            "JOIN agent_traces t USING (agent_trace_id) WHERE kind='nota_fiscal'"
        ).fetchone()
        assert invoice is not None
        assert json.loads(invoice[0])["valor"] is None
        assert "Nota sintetica original" in json.loads(invoice[1])["texto_pdf"]
        for completed in (first, second):
            manifest = (
                workspace / "runs" / run_id(completed) / "manifest.json"
            ).read_text()
            assert str(extraction[0]) in manifest
            assert extraction[1] in manifest
        assert db.execute(
            "SELECT count(*) FROM agent_traces WHERE run_id=?", [run_id(third)]
        ).fetchone() == (0,)


def test_zero_budget_and_invalid_sample_are_recorded_without_calls(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPSIU_AGENTS__MAX_CALLS_PER_RUN", "0")
    zero = invoke("explain")
    invalid = invoke("explain", "--amostra", "0", expected=2)
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM events WHERE run_id=? AND kind='budget.exhausted'",
            [run_id(zero)],
        ).fetchone() == (1,)
    manifest = json.loads(
        (workspace / "runs" / run_id(invalid) / "manifest.json").read_text()
    )
    assert manifest["status"] == "aborted"


def scripted_output(recommendation: str) -> dict[str, JsonValue]:
    return {
        "fundamento": "Resposta sintética para verificar o contrato de integração.",
        "confianca": "baixa",
        "evidencias_citadas": ["achado.evidencia_json"],
        "recomendacao": recommendation,
    }


def test_invalid_agent_output_records_failure_and_can_be_retried(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke("check", "--regras", "R08")

    def invalid_runtime(
        scenarios: Iterable[ScriptedAgentScenario],
    ) -> ScriptedAgentRuntime:
        del scenarios
        return build_scripted_runtime(
            [
                ScriptedAgentScenario(
                    result=AgentExecutionResult.succeeded(scripted_output("aprovado"))
                )
            ]
        )

    with monkeypatch.context() as patch:
        patch.setattr(gateway, "build_scripted_runtime", invalid_runtime)
        failed = invoke("explain", expected=1)
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (0,)
        row = db.execute("SELECT status, output_json FROM agent_traces").fetchone()
        assert row is not None and row[0] == "failed"
        assert json.loads(row[1])["recomendacao"] == "aprovado"
        assert db.execute(
            "SELECT status FROM runs WHERE run_id=?", [run_id(failed)]
        ).fetchone() == ("completed_with_errors",)
        assert db.execute(
            "SELECT count(*) FROM steps WHERE run_id=? AND status='failed'",
            [run_id(failed)],
        ).fetchone() == (1,)
    invoke("explain")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT count(*) FROM agent_traces").fetchone() == (2,)
        assert db.execute("SELECT count(*) FROM pareceres").fetchone() == (1,)


def test_scripted_annotations_do_not_suppress_configured_live_model(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke("check", "--regras", "R08")
    invoke("explain")
    requested_models: list[str] = []

    def fake_codex_runtime(
        settings: Settings, *, repo_root: Path | None = None
    ) -> ScriptedAgentRuntime:
        del repo_root
        requested_models.append(settings.agents.model)
        return build_scripted_runtime(
            [
                ScriptedAgentScenario(
                    result=AgentExecutionResult.succeeded(scripted_output("revisar"))
                )
            ]
        )

    monkeypatch.setattr(gateway, "build_codex_runtime", fake_codex_runtime)
    monkeypatch.setenv("AGENTRIG_RUN_LIVE", "1")
    invoke("explain")
    assert requested_models == ["gpt-5.6-terra"]
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute("SELECT model FROM pareceres ORDER BY model").fetchall() == [
            ("gpt-5.6-terra",),
            ("scripted",),
        ]
        assert db.execute(
            "SELECT model FROM agent_traces ORDER BY model"
        ).fetchall() == [
            ("gpt-5.6-terra",),
            ("scripted",),
        ]

"""TSE CLI imports and R11 checks retain replayable, immutable evidence."""

import csv
import io
import json
import os
import threading
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
BASE_EXPENSE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"
HEADERS = (
    "ANO_ELEICAO",
    "CD_TIPO_ELEICAO",
    "NR_TURNO",
    "DT_ELEICAO",
    "SQ_CANDIDATO",
    "NR_CPF_CANDIDATO",
    "NM_CANDIDATO",
    "SG_UF",
    "DS_CARGO",
)


def candidate_zip(rows: list[tuple[str, str, str, str]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, delimiter=";")
    writer.writerow(HEADERS)
    for candidate_id, cpf, name, uf in rows:
        writer.writerow(
            [2024, 2, 1, "06/10/2024", candidate_id, cpf, name, uf, "PREFEITO"]
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "consulta_cand_2024_BRASIL.csv", text.getvalue().encode("latin1")
        )
    return buffer.getvalue()


@dataclass
class TseServer:
    url: str
    payload: bytes
    requests: int = 0


@pytest.fixture
def tse_server() -> Iterator[TseServer]:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = TseServer(
        "",
        candidate_zip(
            [
                ("100", "00000010155", "Nome eleitoral diferente", "DF"),
                ("200", "#NULO#", "José de Ávila", "SP"),
                ("300", "00000030355", "Nome Coincidente", "RJ"),
            ]
        ),
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(state.payload)))
            self.end_headers()
            self.wfile.write(state.payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}/candidates.zip"
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tse_server: TseServer
) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2024\nto=2024\n'
        f'[external]\ntse_2024_url="{tse_server.url}"\n',
        encoding="utf-8",
    )
    base: dict[str, object] = json.loads(BASE_EXPENSE.read_text())["dados"][0]
    election = date(2024, 10, 6)
    rows: list[dict[str, object]] = []
    for index, offset in enumerate((120, 121, 0, 1, 30, 30, 30, 30, 30, 30), start=1):
        issued = election - timedelta(days=offset)
        row = dict(
            base,
            ano=2024,
            mes=issued.month,
            dataEmissao=issued.isoformat(),
            numeroSubCota="5",
            descricao="DIVULGAÇÃO DA ATIVIDADE PARLAMENTAR.",
            valorDocumento="10.00",
            valorLiquido="10.00",
            valorGlosa="0.00",
            idDocumento=str(202400000 + index),
            numero=f"TSE-{index}",
        )
        if index == 5:
            row.update(
                cpf="",
                nomeParlamentar="JOSE DE AVILA",
                siglaUF="SP",
                numeroDeputadoID=200,
            )
        elif index == 6:
            row.update(
                cpf="00000040455",
                nomeParlamentar="Nome Coincidente",
                siglaUF="RJ",
                numeroDeputadoID=300,
            )
        elif index == 7:
            row.update(
                cpf="",
                nomeParlamentar="Jose de Avila",
                siglaUF="RJ",
                numeroDeputadoID=400,
            )
        elif index == 8:
            row["numeroSubCota"] = "1"
        elif index == 9:
            row["dataEmissao"] = ""
        elif index == 10:
            row.update(
                cpf="00000050555",
                nomeParlamentar="Sem Candidatura",
                numeroDeputadoID=500,
            )
        rows.append(row)
    (tmp_path / "input/Ano-2024.json").write_text(
        json.dumps({"dados": rows}), encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["ingest"])
    assert result.exit_code == 0, result.exception or result.output
    return tmp_path


def invoke(*arguments: str, expected: int = 0) -> str:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.output or result.exception
    return result.stdout.splitlines()[0].removeprefix("run_id: ")


def findings(root: Path) -> list[tuple[object, ...]]:
    with connect_database(root / "db.duckdb", read_only=True) as db:
        return db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()


def test_download_check_and_offline_replay_preserve_original_candidate_provenance(
    workspace: Path,
    tse_server: TseServer,
) -> None:
    invoke("check", "--regras", "R11", expected=1)
    assert findings(workspace) == []
    invoke("fetch", "tse", "--ano", "2024")
    invoke("check", "--regras", "R11")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT id_documento FROM findings ORDER BY id_documento"
        ).fetchall() == [
            (202400001,),
            (202400004,),
            (202400005,),
        ]
        snapshot_id, sha256 = db.execute(
            "SELECT snapshot_id,sha256 FROM snapshots"
        ).fetchone() or (None, None)
        evidence = db.execute(
            "SELECT evidence_json FROM findings ORDER BY id_documento"
        ).fetchall()
        assert all(str(snapshot_id) in row[0] and sha256 in row[0] for row in evidence)
        assert [json.loads(row[0])["metodo_correspondencia"] for row in evidence] == [
            "cpf",
            "cpf",
            "nome_uf",
        ]
    original = findings(workspace)
    invoke("fetch", "tse", "--ano", "2024")
    invoke("check", "--regras", "R11")
    assert findings(workspace) == original
    count = tse_server.requests
    invoke("fetch", "tse", "--ano", "2024", "--replay")
    invoke("fetch", "tse", "--ano", "2024", "--snapshot-id", str(snapshot_id))
    invoke("check", "--regras", "R11")
    assert findings(workspace) == original
    assert tse_server.requests == count


def test_failed_candidate_load_keeps_previous_current_source_and_empty_load_supersedes(
    workspace: Path,
    tse_server: TseServer,
) -> None:
    invoke("fetch", "tse", "--ano", "2024")
    invoke("check", "--regras", "R11")
    original = findings(workspace)
    tse_server.payload = b"invalid;candidate;schema\n"
    invoke("fetch", "tse", "--ano", "2024", expected=1)
    invoke("check", "--regras", "R11")
    assert findings(workspace) == original
    tse_server.payload = candidate_zip([])
    invoke("fetch", "tse", "--ano", "2024")
    invoke("check", "--regras", "R11")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT status,count(*) FROM findings GROUP BY status"
        ).fetchall() == [("superseded", 3)]
        assert db.execute("SELECT count(*) FROM snapshots").fetchone() == (3,)

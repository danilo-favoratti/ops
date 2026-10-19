"""Manual Receita snapshots support exact-branch review and stable replay."""

import csv
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.interfaces.cli import app

pytestmark = pytest.mark.integration
BASE_EXPENSE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"


def write_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="latin1", newline="") as stream:
        csv.writer(stream, delimiter=";", quoting=csv.QUOTE_ALL).writerows(rows)


def establishment(root: str, order: str, cnae: str) -> list[str]:
    row = [""] * 30
    row[0:6] = [root, order, "00", "1" if order == "0001" else "2", "", "02"]
    row[10:13] = ["20200101", cnae, ""]
    return row


def partner(root: str, name: str, kind: str = "2") -> list[str]:
    return [root, kind, name, "***000000**", "49", "20200101", "", "", "", "", "5"]


def expense(index: int, cnpj: str, category: int) -> dict[str, object]:
    base: dict[str, object] = json.loads(BASE_EXPENSE.read_text())["dados"][0]
    return dict(
        base,
        idDocumento=str(209910000 + index),
        cnpjCPF=cnpj,
        numeroSubCota=str(category),
        nomeParlamentar="Ana Quirino",
        dataEmissao="2099-01-01",
        mes=1,
        numero=f"CNPJ-{index}",
        valorDocumento="25.00",
        valorLiquido="25.00",
        valorGlosa="0.00",
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "input").mkdir()
    dump = tmp_path / "dump"
    dump.mkdir()
    (tmp_path / "opsiu.toml").write_text(
        '[paths]\ndata_dir="input"\ndb_path="db.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[years]\nfrom=2099\nto=2099\n',
        encoding="utf-8",
    )
    companies = [
        ("11111111", "Comércio Sintético"),
        ("22222222", "Fornecedor Sintético"),
        ("33333333", "Transportes Sintéticos"),
        ("44444444", "Locação sem motorista"),
        ("55555555", "Supermercado Sintético"),
        ("66666666", "Restaurante do Mercado"),
        ("77777777", "Empresa Fora do Recorte"),
    ]
    write_rows(
        dump / "Empresas0.csv",
        [[root, name, "2062", "49", "1000,00", "01", ""] for root, name in companies],
    )
    write_rows(
        dump / "Estabelecimentos0.csv",
        [
            establishment("11111111", "0001", "4711302"),
            establishment("11111111", "0002", "5611201"),
            establishment("22222222", "0001", "8299799"),
            establishment("33333333", "0001", "4923002"),
            establishment("44444444", "0001", "7711000"),
            establishment("55555555", "0001", "5611201"),
            establishment("66666666", "0001", "5611201"),
            establishment("77777777", "0001", "4711302"),
        ],
    )
    write_rows(
        dump / "Socios0.csv",
        [
            partner("22222222", "Bruno Quirino"),
            partner("33333333", "Marcos Silva"),
            partner("44444444", "Ana Duarte"),
            partner("66666666", "Empresa Quirino", kind="1"),
            partner("77777777", "Bruno Quirino"),
        ],
    )
    rows = [
        expense(1, "11111111000100", 13),
        expense(2, "11111111000200", 13),
        expense(3, "22222222000100", 1),
        expense(4, "33333333000100", 120),
        expense(5, "44444444000100", 120),
        expense(6, "66666666000100", 13),
    ]
    rows[3].update(
        nomeParlamentar="Carlos Silva", cpf="00000020255", numeroDeputadoID=202
    )
    (tmp_path / "input/Ano-2099.json").write_text(
        json.dumps({"dados": rows}), encoding="utf-8"
    )
    invoke("ingest")
    return tmp_path


def invoke(*arguments: str, expected: int = 0) -> str:
    result = CliRunner().invoke(app, list(arguments))
    assert result.exit_code == expected, result.output or result.exception
    return result.stdout.splitlines()[0].removeprefix("run_id: ")


def findings(root: Path) -> list[tuple[object, ...]]:
    with connect_database(root / "db.duckdb", read_only=True) as db:
        return db.execute("SELECT * FROM findings ORDER BY finding_key").fetchall()


def test_filtered_import_matches_exact_branch_and_replays_without_source_files(
    workspace: Path,
) -> None:
    invoke("fetch", "cnpj", "--from", str(workspace / "dump"))
    invoke("check", "--regras", "R12,R13")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT rule_id,id_documento FROM findings ORDER BY rule_id,id_documento"
        ).fetchall() == [
            ("R12", 209910003),
            ("R13", 209910001),
            ("R13", 209910004),
        ]
    original = findings(workspace)
    invoke("fetch", "cnpj", "--from", str(workspace / "dump"))
    invoke("check", "--regras", "R12,R13")
    assert findings(workspace) == original
    (workspace / "dump").rename(workspace / "original-input-unavailable")
    invoke("fetch", "cnpj", "--replay")
    invoke("check", "--regras", "R12,R13")
    assert findings(workspace) == original


def test_same_dump_reload_covers_new_current_root_without_changing_old_findings(
    workspace: Path,
) -> None:
    invoke("fetch", "cnpj", "--from", str(workspace / "dump"))
    invoke("check", "--regras", "R12,R13")
    original = findings(workspace)
    source = workspace / "input/Ano-2099.json"
    data = json.loads(source.read_text())
    data["dados"].append(expense(7, "55555555000100", 13))
    source.write_text(json.dumps(data), encoding="utf-8")
    invoke("ingest")
    invoke("check", "--regras", "R12,R13", expected=1)
    assert findings(workspace) == original
    invoke("fetch", "cnpj", "--from", str(workspace / "dump"))
    invoke("check", "--regras", "R12,R13")
    current = findings(workspace)
    assert len(current) == len(original) + 1
    assert all(row in current for row in original)
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT rule_id FROM findings WHERE id_documento=209910007"
        ).fetchone() == ("R13",)


def test_failed_parse_retains_prior_import_and_successful_empty_dump_supersedes(
    workspace: Path,
) -> None:
    dump = workspace / "dump"
    invoke("fetch", "cnpj", "--from", str(dump))
    invoke("check", "--regras", "R12,R13")
    original = findings(workspace)
    with (dump / "Socios0.csv").open("a", encoding="latin1") as stream:
        stream.write('"22222222";"incomplete"\n')
    invoke("fetch", "cnpj", "--from", str(dump), expected=1)
    invoke("check", "--regras", "R12,R13")
    assert findings(workspace) == original
    for path in dump.iterdir():
        path.write_bytes(b"")
    invoke("fetch", "cnpj", "--from", str(dump))
    invoke("check", "--regras", "R12,R13")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT status,count(*) FROM findings GROUP BY status"
        ).fetchall() == [("superseded", 3)]


def test_r13_repeated_accounting_rows_preserve_adjustments_and_replay(
    workspace: Path,
) -> None:
    source = workspace / "input/Ano-2099.json"
    data = json.loads(source.read_text())
    original = data["dados"][0]
    data["dados"].extend(
        [
            dict(original, valorLiquido="10.00"),
            dict(original, valorLiquido="-5.00"),
            dict(original, valorLiquido="0.00"),
            dict(original, numeroSubCota="1", valorLiquido="1000.00"),
        ]
    )
    source.write_text(json.dumps(data), encoding="utf-8")
    invoke("ingest")
    invoke("fetch", "cnpj", "--from", str(workspace / "dump"))
    invoke("check", "--regras", "R13")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        row = db.execute(
            "SELECT rule_version,severity,amount,evidence_json FROM findings "
            "WHERE rule_id='R13' AND id_documento=209910001"
        ).fetchone()
        assert row is not None
        version, severity, amount, raw = row
        assert (version, severity, amount) == ("2", "revisar", Decimal("30.00"))
        evidence = json.loads(raw)
        assert sorted(Decimal(row["valor"]) for row in evidence["ocorrencias"]) == [
            Decimal("-5.00"),
            Decimal("0.00"),
            Decimal("10.00"),
            Decimal("25.00"),
        ]
        assert {row["registro_sha256"] for row in evidence["registros_origem"]} == {
            row[0]
            for row in db.execute(
                "SELECT record_sha256 FROM despesas_current "
                "WHERE id_documento=209910001"
            ).fetchall()
        }
        assert len(evidence["registros_origem"]) == 5
    saved = findings(workspace)
    invoke("check", "--regras", "R13")
    assert findings(workspace) == saved
    invoke("ingest", "--force")
    invoke("check", "--regras", "R13")
    assert findings(workspace) == saved


def test_r12_zero_documents_are_separate_records_with_stable_history(
    workspace: Path,
) -> None:
    from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
    from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
    from opsiu.domain.findings import source_record_identity

    source = workspace / "input/Ano-2099.json"
    data = json.loads(source.read_text())
    original = data["dados"][2]
    zero_a = dict(original, idDocumento=0, valorLiquido="12.34")
    zero_b = dict(
        original,
        idDocumento=0,
        nomeParlamentar="Bruna Quirino",
        numeroDeputadoID=202,
        valorLiquido="56.78",
    )
    data["dados"].extend(
        [
            zero_a,
            zero_b,
            dict(original, valorLiquido="-5.00"),
            dict(original, nomeParlamentar="Ana Silva", valorLiquido="999.00"),
        ]
    )
    source.write_text(json.dumps(data), encoding="utf-8")
    invoke("ingest")
    invoke("fetch", "cnpj", "--from", str(workspace / "dump"))
    invoke("check", "--regras", "R12")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        rows = db.execute(
            "SELECT finding_key, record_identity, record_sha256, amount FROM findings "
            "WHERE id_documento=0 ORDER BY amount"
        ).fetchall()
        assert len(rows) == 2 and rows[0][0] != rows[1][0]
        assert [row[3] for row in rows] == [Decimal("12.34"), Decimal("56.78")]
        for key, discriminator, record_hash, _ in rows:
            assert discriminator == source_record_identity(2099, record_hash)
            detail = DuckDBReviewRepository(db).get_finding(key)
            assert detail is not None
            assert detail.record.finding.finding_key == key
            assert detail.expense.record_sha256 == record_hash
        exported = list(DuckDBFindingExportReader(db).active_findings())
        assert len(exported) == 3
        assert db.execute(
            "SELECT rule_version,amount FROM findings WHERE id_documento=209910003"
        ).fetchone() == ("2", Decimal("20.00"))
        names = {
            record.nome_parlamentar
            for record in exported
            if record.finding.id_documento == 0
        }
        assert names == {"Ana Quirino", "Bruna Quirino"}
    saved = findings(workspace)
    invoke("check", "--regras", "R12")
    assert findings(workspace) == saved
    invoke("ingest", "--force")
    invoke("check", "--regras", "R12")
    assert findings(workspace) == saved
    invoke("explain", "--regras", "R12", "--amostra", "10")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT count(DISTINCT finding_key) FROM pareceres"
        ).fetchone() == (3,)


def test_r12_record_keys_survive_relocation_and_reordering_but_track_changes(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = workspace / "input/Ano-2099.json"
    zero_a = dict(expense(1, "22222222000100", 1), idDocumento=0)
    zero_b = dict(zero_a, nomeParlamentar="Bruna Quirino", numeroDeputadoID=202)
    source.write_text(json.dumps({"dados": [zero_a, zero_a, zero_b]}))
    invoke("ingest")
    invoke("fetch", "cnpj", "--from", str(workspace / "dump"))
    invoke("check", "--regras", "R12")
    saved = findings(workspace)
    assert len(saved) == 2
    (workspace / "input").rename(workspace / "relocated")
    monkeypatch.setenv("OPSIU_PATHS__DATA_DIR", "relocated")
    invoke("ingest", "--force")
    invoke("check", "--regras", "R12")
    assert findings(workspace) == saved
    relocated = workspace / "relocated/Ano-2099.json"
    relocated.write_text(json.dumps({"dados": [zero_b, zero_a, zero_a]}))
    invoke("ingest")
    invoke("check", "--regras", "R12")
    assert findings(workspace) == saved
    relocated.write_text(json.dumps({"dados": [dict(zero_b, valorLiquido="30.00")]}))
    invoke("ingest")
    invoke("check", "--regras", "R12")
    with connect_database(workspace / "db.duckdb", read_only=True) as db:
        assert db.execute(
            "SELECT status,count(*) FROM findings GROUP BY status ORDER BY status"
        ).fetchall() == [("active", 1), ("superseded", 2)]
        assert db.execute(
            "SELECT amount FROM findings WHERE status='active'"
        ).fetchone() == (Decimal("30.00"),)

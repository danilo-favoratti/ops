"""Final report inputs retain current approval and immutable source evidence."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.annotation_repository import DuckDBAnnotationRepository
from opsiu.adapters.duckdb.final_report import DuckDBFinalReportReader
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.repository import DuckDBExpenseRepository
from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
from opsiu.application.ports import (
    AnnotationKind,
    AnnotationResult,
    AnnotationSpec,
    ExpenseSourceMetadata,
    JsonValue,
)
from opsiu.domain.decisions import Decision, DecisionValue
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import finding_key
from opsiu.domain.models import Despesa

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 2, tzinfo=UTC)
LATER = NOW + timedelta(days=1)


def identity(number: int) -> str:
    return f"00000000-0000-7000-8000-{number:012d}"


def insert(
    connection: DuckDBPyConnection, table: str, values: dict[str, object]
) -> None:
    connection.execute(
        f"INSERT INTO {table} ({', '.join(values)}) "
        f"VALUES ({', '.join('?' for _ in values)})",
        list(values.values()),
    )


def expense(document: int = 101, *, deputy: int = 7, uf: str = "SP") -> Despesa:
    return Despesa.from_raw(
        {
            "nomeParlamentar": f"Deputado {deputy}",
            "siglaUF": uf,
            "siglaPartido": "ABC",
            "numeroSubCota": 1,
            "fornecedor": "Fornecedor original",
            "cnpjCPF": "12345678000190",
            "numero": "NF-123",
            "dataEmissao": "2025-10-01",
            "valorDocumento": "123.45",
            "valorGlosa": "0.01",
            "valorLiquido": "123.44",
            "ano": 2025,
            "mes": 10,
            "numeroDeputadoID": deputy,
            "idDocumento": document,
            "urlDocumento": f"https://example.invalid/{document}.pdf",
        }
    )


def source(
    connection: DuckDBPyConnection,
    expenses: tuple[Despesa, ...],
    *,
    number: int = 10,
) -> None:
    with DuckDBExpenseRepository(connection).stage(
        source_file_id=identity(number),
        source_year=2025,
        run_id=identity(1),
        step_id=identity(2),
    ) as stage:
        stage.add_batch(expenses)
        stage.publish(
            ExpenseSourceMetadata(f"source-{number}.json", "a" * 64, 1234),
            ingested_at=NOW if number == 10 else LATER,
        )


@pytest.fixture
def database() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect() as connection:
        apply_migrations(connection)
        connection.execute("SET TimeZone = 'America/Sao_Paulo'")
        connection.execute(
            "INSERT INTO runs VALUES (?, 'review.decide', '{}', ?, 'commit', "
            "false, ?, NULL, 'running', '{}')",
            [identity(1), "a" * 64, NOW],
        )
        connection.execute(
            "INSERT INTO steps VALUES (?, ?, 'decide', ?, NULL, 'running')",
            [identity(2), identity(1), NOW],
        )
        # A repeated document with a different hash must not replace its origin.
        source(connection, (expense(deputy=99), expense(), expense(), expense(102)))
        yield connection


def finding(
    database: DuckDBPyConnection, *, rule: str = "R08", document: int = 101
) -> str:
    key = finding_key(rule, "1", document)
    insert(
        database,
        "finding_records",
        {
            "finding_key": key,
            "rule_id": rule,
            "rule_version": "1",
            "id_documento": document,
            "severity": "alta",
            "amount": Decimal("123.44"),
            "evidence_json": '{"motivo":"Evidência original."}',
            "run_id": identity(1),
            "step_id": identity(2),
            "source_file_id": identity(10),
            "record_sha256": expense(document).record_sha256,
            "code_commit": "original-commit",
            "created_at": NOW,
        },
    )
    return key


def decide(
    database: DuckDBPyConnection,
    key: str,
    number: int,
    value: DecisionValue = DecisionValue.APPROVED,
    *,
    at: datetime = NOW,
) -> Decision:
    result = Decision(
        identity(number),
        key,
        value,
        "Revisora original",
        "Verificado.",
        at,
        identity(1),
        identity(2),
    )
    DuckDBReviewRepository(database).append_decision(result)
    return result


def annotation(
    database: DuckDBPyConnection,
    key: str,
    number: int,
    *,
    kind: AnnotationKind = "parecer",
    at: datetime = NOW,
    model: str = "scripted",
) -> str:
    name = "explicar-achado" if kind == "parecer" else "revisar-nota"
    agent = f"{name}-R08" if kind == "parecer" else name
    output: dict[str, JsonValue] = {
        "fundamento": f"Parecer histórico {number}.",
        "confianca": "baixa",
        "evidencias_citadas": ["registro.fornecedor"],
        "recomendacao": "revisar",
    }
    insert(
        database,
        "agent_traces",
        {
            "agent_trace_id": identity(number),
            "run_id": identity(1),
            "step_id": identity(2),
            "finding_key": key,
            "agent_id": agent,
            "agent_version": "1",
            "model": model,
            "prompt_name": name,
            "prompt_version": "1",
            "prompt_sha256": "b" * 64,
            "input_schema": "input",
            "output_schema": "output",
            "instructions": "Anotar apenas a evidência fornecida.",
            "input_json": "{}",
            "output_json": json.dumps(output),
            "usage_json": "{}",
            "failure_json": None,
            "status": "succeeded",
            "started_at": at,
            "finished_at": at,
            "duration_ms": 1.0,
        },
    )
    opinion_id = identity(number + 1_000)
    DuckDBAnnotationRepository(database).save(
        key,
        AnnotationSpec(kind, agent, model, name, "1", "b" * 64),
        AnnotationResult(identity(number), output, None),
        parecer_id=opinion_id,
        created_at=at,
        run_id=identity(1),
        step_id=identity(2),
    )
    return opinion_id


def test_only_latest_approved_active_findings_are_selected(
    database: DuckDBPyConnection,
) -> None:
    keys = {
        rule: finding(database, rule=rule)
        for rule in ("R01", "R02", "R03", "R04", "R05", "R06")
    }
    # The higher UUID wins ties, independently of insertion order.
    expected = decide(database, keys["R01"], 102, at=LATER)
    decide(database, keys["R01"], 101, DecisionValue.REJECTED, at=LATER)
    for rule, rejected in (
        ("R02", DecisionValue.REJECTED),
        ("R03", DecisionValue.REVIEW),
    ):
        decide(database, keys[rule], 110 + int(rule[1:]))
        decide(database, keys[rule], 120 + int(rule[1:]), rejected, at=LATER)
    decide(database, keys["R04"], 104)
    database.execute(
        "INSERT INTO finding_supersessions VALUES (?, ?, ?)",
        [keys["R04"], LATER, identity(1)],
    )
    # An older approval inserted last must not overrule a newer rejection.
    decide(database, keys["R06"], 106, DecisionValue.REJECTED, at=LATER)
    decide(database, keys["R06"], 206)
    result = tuple(DuckDBFinalReportReader(database).approved_findings())
    assert len(result) == 1
    assert result[0].record.finding.finding_key == keys["R01"]
    assert result[0].decision == expected
    assert result[0].pareceres == ()


def test_original_expense_source_and_hash_survive_replacement(
    database: DuckDBPyConnection,
) -> None:
    key = finding(database)
    approved = decide(database, key, 100)
    source(database, (expense(deputy=999, uf="RJ"),), number=11)
    (item,) = DuckDBFinalReportReader(database).approved_findings()
    record = item.record
    assert record.numero_deputado_id == 7
    assert record.nome_parlamentar == "Deputado 7" and record.sigla_uf == "SP"
    assert record.source_record_number == 2
    assert record.source_size_bytes == 1234
    assert record.valor_documento == Decimal("123.45")
    assert record.valor_glosa == Decimal("0.01")
    assert record.valor_liquido == Decimal("123.44")
    assert record.finding.amount == Decimal("123.44")
    assert record.finding.provenance.source.source_file_id == identity(10)
    assert record.finding.provenance.source.source_file == "source-10.json"
    assert record.finding.provenance.source.source_sha256 == "a" * 64
    assert record.finding.provenance.source.record_sha256 == expense().record_sha256
    assert record.finding.provenance.run_id == identity(1)
    assert record.finding.provenance.step_id == identity(2)
    assert record.finding.provenance.code_commit == "original-commit"
    assert record.finding.provenance.created_at == NOW
    assert item.decision == approved


def test_only_matching_opinion_history_is_included_in_chronological_order(
    database: DuckDBPyConnection,
) -> None:
    key = finding(database)
    other = finding(database, document=102)
    decide(database, key, 100)
    latest = annotation(database, key, 302, at=LATER, model="other-model")
    second = annotation(database, key, 301)
    first = annotation(database, key, 300)
    annotation(database, key, 303, kind="nota_fiscal")
    annotation(database, other, 304)
    annotation(database, key, 305)
    database.execute(
        "UPDATE agent_traces SET status = 'failed', failure_json = '{}' "
        "WHERE agent_trace_id = ?",
        [identity(305)],
    )
    (item,) = DuckDBFinalReportReader(database).approved_findings()
    assert [opinion.parecer_id for opinion in item.pareceres] == [first, second, latest]
    assert [opinion.agent_trace_id for opinion in item.pareceres] == [
        identity(300),
        identity(301),
        identity(302),
    ]
    assert [opinion.model for opinion in item.pareceres] == [
        "scripted",
        "scripted",
        "other-model",
    ]
    assert [opinion.created_at for opinion in item.pareceres] == [NOW, NOW, LATER]
    assert all(
        opinion.finding_key == key and opinion.kind == "parecer"
        for opinion in item.pareceres
    )
    assert all(
        opinion.run_id == identity(1) and opinion.step_id == identity(2)
        for opinion in item.pareceres
    )
    assert item.pareceres[0].output["fundamento"] == "Parecer histórico 300."


def test_missing_original_expense_fails_instead_of_substituting_current_source(
    database: DuckDBPyConnection,
) -> None:
    key = finding(database)
    decide(database, key, 100)
    database.execute(
        "UPDATE finding_records SET record_sha256 = ? WHERE finding_key = ?",
        ["f" * 64, key],
    )
    with pytest.raises(DomainError):
        tuple(DuckDBFinalReportReader(database).approved_findings())


def test_stream_uses_one_snapshot_for_findings_decisions_and_opinions(
    database: DuckDBPyConnection,
) -> None:
    keys = (finding(database), finding(database, document=102))
    approvals = {
        key: decide(database, key, 100 + index) for index, key in enumerate(keys)
    }
    stream = DuckDBFinalReportReader(database).approved_findings()
    first = next(stream)
    remaining = next(key for key in keys if key != first.record.finding.finding_key)
    decide(database, remaining, 200, DecisionValue.REJECTED, at=LATER)
    annotation(database, remaining, 300, at=LATER)
    rest = tuple(stream)
    assert len(rest) == 1
    assert rest[0].record.finding.finding_key == remaining
    assert rest[0].decision == approvals[remaining]
    assert rest[0].pareceres == ()
    refreshed = tuple(DuckDBFinalReportReader(database).approved_findings())
    assert len(refreshed) == 1
    assert refreshed[0].record.finding.finding_key == first.record.finding.finding_key

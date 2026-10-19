"""Human decisions retain history and the exact original audit evidence."""

import hashlib
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.pdf_repository import DuckDBPdfRepository
from opsiu.adapters.duckdb.repository import DuckDBExpenseRepository
from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
from opsiu.application.ports import ExpenseSourceMetadata, PdfContent, ReviewFilters
from opsiu.domain.decisions import Decision, DecisionValue
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import Finding, Provenance, SourceOrigin, evidence_json
from opsiu.domain.models import Despesa

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
LATER = NOW + timedelta(days=1)


def identity(number: int) -> str:
    return f"00000000-0000-7000-8000-{number:012d}"


def expense(document: int = 101, *, deputy: int = 7, uf: str = "SP") -> Despesa:
    return Despesa.from_raw(
        {
            "nomeParlamentar": f"Deputado {deputy}",
            "cpf": "12345678901",
            "numeroCarteiraParlamentar": 42,
            "legislatura": 2023,
            "siglaUF": uf,
            "siglaPartido": "ABC",
            "codigoLegislatura": 57,
            "numeroSubCota": 1,
            "descricao": "Manutenção de escritório",
            "numeroEspecificacaoSubCota": 2,
            "descricaoEspecificacao": "Despesa original",
            "fornecedor": "Fornecedor original",
            "cnpjCPF": "12345678000190",
            "numero": "NF-123",
            "tipoDocumento": 0,
            "dataEmissao": "2025-10-01",
            "valorDocumento": "123.45",
            "valorGlosa": "0.01",
            "valorLiquido": "123.44",
            "ano": 2025,
            "mes": 10,
            "parcela": 1,
            "passageiro": "Pessoa original",
            "trecho": "BSB-SP",
            "lote": 555,
            "ressarcimento": 3,
            "datPagamentoRestituicao": "2025-10-02",
            "restituicao": "1.23",
            "numeroDeputadoID": deputy,
            "idDocumento": document,
            "urlDocumento": f"https://example.invalid/{document}.pdf",
        }
    )


def source(
    connection: DuckDBPyConnection,
    expenses: tuple[Despesa, ...],
    *,
    number: int = 20,
) -> None:
    with DuckDBExpenseRepository(connection).stage(
        source_file_id=identity(number),
        source_year=2025,
        run_id=identity(1),
        step_id=identity(3),
    ) as stage:
        stage.add_batch(expenses)
        stage.publish(
            ExpenseSourceMetadata(f"source-{number}.json", "a" * 64, 1234),
            ingested_at=NOW if number == 20 else LATER,
        )


@pytest.fixture
def database() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect() as connection:
        apply_migrations(connection)
        for number in (1, 2):
            connection.execute(
                "INSERT INTO runs VALUES (?, 'review.decide', '{}', ?, 'commit', "
                "false, ?, NULL, 'running', '{}')",
                [identity(number), "a" * 64, NOW],
            )
            connection.execute(
                "INSERT INTO steps VALUES (?, ?, 'decide', ?, NULL, 'running')",
                [identity(number + 2), identity(number), NOW],
            )
        # Duplicate raw records still resolve to the earliest archived position.
        source(connection, (expense(), expense(102, deputy=8, uf="RJ"), expense()))
        yield connection


def finding(
    database: DuckDBPyConnection,
    *,
    rule: str = "R08",
    document: int = 101,
    severity: str = "alta",
    amount: str = "123.44",
) -> Finding:
    row = database.execute(
        "SELECT record_sha256 FROM despesas "
        "WHERE source_file_id = ? AND id_documento = ? LIMIT 1",
        [identity(20), document],
    ).fetchone()
    assert row is not None
    result = Finding(
        rule,
        "1",
        document,
        severity,
        Decimal(amount),
        {"motivo": "Evidência original.", "valor": "123.44"},
        Provenance(
            identity(1),
            identity(3),
            SourceOrigin(identity(20), "source-20.json", "a" * 64, str(row[0])),
            "commit",
            NOW,
        ),
    )
    database.execute(
        "INSERT INTO finding_records VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
        [
            result.finding_key,
            rule,
            "1",
            document,
            severity,
            result.amount,
            evidence_json(result.evidence),
            identity(1),
            identity(3),
            identity(20),
            row[0],
            "commit",
            NOW,
        ],
    )
    return result


def decision(
    key: str,
    value: DecisionValue = DecisionValue.APPROVED,
    *,
    number: int = 100,
    at: datetime = NOW,
) -> Decision:
    return Decision(
        identity(number),
        key,
        value,
        "Revisor",
        "Comentário original.",
        at,
        identity(1),
        identity(3),
    )


def test_append_keeps_every_decision_and_current_uses_time_then_identity(
    database: DuckDBPyConnection,
) -> None:
    item = finding(database)
    repository = DuckDBReviewRepository(database)
    first = decision(item.finding_key)
    higher = decision(item.finding_key, DecisionValue.REVIEW, number=102, at=LATER)
    lower = decision(item.finding_key, DecisionValue.REJECTED, number=101, at=LATER)
    for entry in (first, higher, lower):
        repository.append_decision(entry)
    detail = repository.get_finding(item.finding_key)
    assert detail is not None
    assert detail.current_decision == higher
    assert detail.decisions == (first, lower, higher)
    assert detail.record.finding == item
    assert detail.expense == expense()
    assert detail.annotations == ()
    assert detail.pdf is None
    pending = repository.list_pending(ReviewFilters())
    assert len(pending) == 1 and pending[0].current_decision == higher
    assert database.execute(
        "SELECT CAST(decision_id AS VARCHAR), decision, reviewer, comment, decided_at, "
        "CAST(decision_run_id AS VARCHAR), CAST(decision_step_id AS VARCHAR) "
        "FROM findings_current WHERE finding_key = ?",
        [item.finding_key],
    ).fetchone() == (
        higher.decision_id,
        higher.decision.value,
        higher.reviewer,
        higher.comment,
        higher.decided_at,
        higher.run_id,
        higher.step_id,
    )


def test_pending_priority_pagination_and_latest_decision_exclusion(
    database: DuckDBPyConnection,
) -> None:
    repository = DuckDBReviewRepository(database)
    expected = [
        finding(database, rule="R01", amount="10"),
        finding(database, rule="R02", amount="10"),
        finding(database, rule="R03", severity="media", amount="500"),
        finding(database, rule="R04", severity="revisar", amount="900"),
    ]
    for number, value in ((5, DecisionValue.APPROVED), (6, DecisionValue.REJECTED)):
        item = finding(database, rule=f"R{number:02d}")
        repository.append_decision(
            decision(item.finding_key, number=100 + number, value=value)
        )
    retired = finding(database, rule="R07")
    database.execute(
        "INSERT INTO finding_supersessions VALUES (?, ?, ?)",
        [retired.finding_key, LATER, identity(2)],
    )
    priority = {"alta": 0, "media": 1, "revisar": 2}
    expected.sort(
        key=lambda item: (priority[item.severity], -item.amount, item.finding_key)
    )
    assert [
        item.record.finding for item in repository.list_pending(ReviewFilters())
    ] == expected
    assert [
        item.record.finding
        for item in repository.list_pending(ReviewFilters(limit=2, offset=1))
    ] == expected[1:3]
    assert [
        item.record.finding
        for item in repository.list_pending(ReviewFilters(min_amount=Decimal("11")))
    ] == expected[2:]
    assert repository.list_pending(ReviewFilters(offset=99)) == ()
    assert repository.count_pending(ReviewFilters(limit=2, offset=99)) == 4
    assert repository.count_pending(ReviewFilters(min_amount=Decimal("11"))) == 2
    assert repository.count_pending(ReviewFilters(rule_id="R01")) == 1
    assert repository.count_pending(ReviewFilters(deputy_id=999)) == 0
    assert repository.count_pending(ReviewFilters(uf="BA")) == 0


def test_filters_and_detail_use_archived_expense_after_source_replacement(
    database: DuckDBPyConnection,
) -> None:
    original = finding(database)
    finding(database, rule="R09", document=102)
    source(database, (expense(deputy=99, uf="BA"),), number=21)
    repository = DuckDBReviewRepository(database)
    pending = repository.list_pending(
        ReviewFilters(rule_id="R08", deputy_id=7, uf="sp", min_amount=Decimal("123.44"))
    )
    assert len(pending) == 1
    assert pending[0].record.finding == original
    assert pending[0].record.source_record_number == 1
    assert pending[0].record.source_size_bytes == 1234
    assert pending[0].record.nome_parlamentar == "Deputado 7"
    selected = ReviewFilters(deputy_ids=(7, 8, 99), ufs=("sp", "RJ", "BA"))
    assert repository.count_pending(selected) == 2
    assert len(repository.list_pending(selected)) == 2
    assert repository.count_pending(ReviewFilters(deputy_ids=(7, 99), ufs=("BA",))) == 0
    assert {option.id: option.name for option in repository.list_deputies()} == {
        7: "Deputado 7",
        8: "Deputado 8",
        99: "Deputado 99",
    }
    assert repository.list_pending(ReviewFilters(deputy_id=99)) == ()
    assert repository.list_pending(ReviewFilters(uf="BA")) == ()
    assert repository.list_pending(ReviewFilters(rule_id="R99")) == ()
    assert repository.list_pending(ReviewFilters(min_amount=Decimal("123.45"))) == ()
    detail = repository.get_finding(original.finding_key)
    assert detail is not None and detail.expense == expense()
    assert repository.get_finding("unknown") is None


def annotation(database: DuckDBPyConnection, key: str, number: int, kind: str) -> None:
    database.execute(
        "INSERT INTO agent_traces VALUES (?, ?, ?, ?, 'agent', '1', 'scripted', "
        "'prompt', '1', ?, 'input', 'output', 'Instrução original.', '{}', "
        "'{\"recomendacao\":\"revisar\"}', '{}', NULL, 'succeeded', ?, ?, 1.0)",
        [identity(number), identity(1), identity(3), key, "b" * 64, NOW, NOW],
    )
    database.execute(
        "INSERT INTO pareceres VALUES (?, ?, ?, 'agent', ?, '1', 'scripted', "
        '\'{"recomendacao":"revisar"}\', ?, ?, ?)',
        [
            identity(number + 10),
            key,
            identity(number),
            kind,
            NOW,
            identity(1),
            identity(3),
        ],
    )


def pdf(database: DuckDBPyConnection, *, number: int, url: str, at: datetime) -> str:
    database.execute(
        "INSERT INTO snapshots VALUES "
        "(?, 'pdf', ?, '{}', ?, 200, '{}', ?, 12, ?, ?, ?)",
        [
            identity(number),
            f"snapshot-{number}.pdf",
            url,
            "c" * 64,
            at,
            identity(1),
            identity(3),
        ],
    )
    text = f"Nota original {number}."
    record = DuckDBPdfRepository(database).publish(
        PdfContent(
            101,
            url,
            identity(number),
            "c" * 64,
            Path(f"text-{number}.txt"),
            hashlib.sha256(text.encode()).hexdigest(),
            len(text),
            "pypdf",
            "1",
            text,
            hashlib.sha256(text.encode()).hexdigest(),
            False,
            1,
        ),
        (),
        extraction_id=identity(number + 10),
        created_at=at,
        run_id=identity(1),
        step_id=identity(3),
    )
    return record.extraction_id


def test_superseded_detail_keeps_all_annotations_and_latest_original_url_pdf(
    database: DuckDBPyConnection,
) -> None:
    original = finding(database)
    repository = DuckDBReviewRepository(database)
    approved = decision(original.finding_key)
    repository.append_decision(approved)
    annotation(database, original.finding_key, 201, "nota_fiscal")
    annotation(database, original.finding_key, 200, "parecer")
    url = "https://example.invalid/101.pdf"
    pdf(database, number=300, url=url, at=NOW)
    latest = pdf(database, number=301, url=url, at=LATER)
    pdf(database, number=302, url=url + "?changed=1", at=LATER + timedelta(days=1))
    database.execute(
        "INSERT INTO finding_supersessions VALUES (?, ?, ?)",
        [original.finding_key, LATER, identity(2)],
    )
    detail = repository.get_finding(original.finding_key)
    assert detail is not None
    assert detail.record.finding.status == "superseded"
    assert detail.record.finding.superseded_at == LATER
    assert detail.record.finding.superseded_by_run_id == identity(2)
    assert detail.current_decision == approved and detail.decisions == (approved,)
    assert [item.parecer_id for item in detail.annotations] == [
        identity(210),
        identity(211),
    ]
    assert [item.kind for item in detail.annotations] == ["parecer", "nota_fiscal"]
    assert all(
        item.output == {"recomendacao": "revisar"} for item in detail.annotations
    )
    assert detail.pdf is not None and detail.pdf.extraction_id == latest
    assert repository.list_pending(ReviewFilters()) == ()


@pytest.mark.parametrize("invalid", ["run", "step", "mismatch", "missing_step"])
def test_write_requires_matching_active_run_and_step(
    database: DuckDBPyConnection,
    invalid: str,
) -> None:
    item = finding(database)
    entry = decision(item.finding_key)
    if invalid == "run":
        database.execute(
            "UPDATE runs SET status='completed' WHERE run_id=?", [identity(1)]
        )
    elif invalid == "step":
        database.execute(
            "UPDATE steps SET status='completed' WHERE step_id=?", [identity(3)]
        )
    else:
        entry = replace(entry, step_id=identity(4 if invalid == "mismatch" else 999))
    with pytest.raises(RuntimeError, match="matching active run and step"):
        DuckDBReviewRepository(database).append_decision(entry)
    assert database.execute("SELECT count(*) FROM decisions").fetchone() == (0,)
    database.execute("BEGIN TRANSACTION")
    database.execute("ROLLBACK")


@pytest.mark.parametrize("retired", [False, True])
def test_missing_or_superseded_finding_cannot_receive_decision(
    database: DuckDBPyConnection,
    retired: bool,
) -> None:
    item = finding(database)
    if retired:
        database.execute(
            "INSERT INTO finding_supersessions VALUES (?, ?, ?)",
            [item.finding_key, LATER, identity(2)],
        )
    with pytest.raises(DomainError, match="active finding"):
        DuckDBReviewRepository(database).append_decision(
            decision(item.finding_key if retired else "unknown")
        )
    assert database.execute("SELECT count(*) FROM decisions").fetchone() == (0,)


def test_duplicate_decision_id_preserves_original_and_rolls_back_cleanly(
    database: DuckDBPyConnection,
) -> None:
    item = finding(database)
    repository = DuckDBReviewRepository(database)
    first = decision(item.finding_key)
    repository.append_decision(first)
    with pytest.raises(duckdb.ConstraintException):
        repository.append_decision(replace(first, decision=DecisionValue.REJECTED))
    repository.append_decision(
        decision(item.finding_key, DecisionValue.REVIEW, number=101, at=LATER)
    )
    detail = repository.get_finding(item.finding_key)
    assert detail is not None and len(detail.decisions) == 2
    assert detail.decisions[0] == first


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewer", " "),
        ("decision", "invalid"),
        ("comment", None),
        ("decided_at", NOW.replace(tzinfo=None)),
    ],
)
def test_adapter_revalidates_decision_before_writing(
    database: DuckDBPyConnection,
    field: str,
    value: object,
) -> None:
    entry = decision(finding(database).finding_key)
    object.__setattr__(entry, field, value)
    with pytest.raises(ValueError):
        DuckDBReviewRepository(database).append_decision(entry)
    assert database.execute("SELECT count(*) FROM decisions").fetchone() == (0,)


def test_adapter_revalidates_filters(database: DuckDBPyConnection) -> None:
    filters = ReviewFilters()
    object.__setattr__(filters, "limit", -1)
    with pytest.raises(ValueError):
        DuckDBReviewRepository(database).list_pending(filters)


def test_missing_original_expense_is_an_explicit_error(
    database: DuckDBPyConnection,
) -> None:
    item = finding(database)
    database.execute(
        "UPDATE finding_records SET record_sha256 = ? WHERE finding_key = ?",
        ["f" * 64, item.finding_key],
    )
    repository = DuckDBReviewRepository(database)
    with pytest.raises(DomainError, match="original expense record"):
        repository.get_finding(item.finding_key)
    with pytest.raises(DomainError, match="original expense record"):
        repository.list_pending(ReviewFilters(deputy_id=999, uf="ZZ"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "invalid"),
        ("finding_key", "unknown"),
        ("run_id", identity(999)),
        ("step_id", identity(999)),
    ],
)
def test_migration_enforces_decision_values_and_foreign_keys(
    database: DuckDBPyConnection,
    field: str,
    value: str,
) -> None:
    entry = decision(finding(database).finding_key)
    fields: dict[str, object] = {
        name: getattr(entry, name) for name in Decision.__dataclass_fields__
    }
    fields[field] = value
    with pytest.raises(duckdb.ConstraintException):
        database.execute(
            "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            list(fields.values()),
        )
    assert database.execute("SELECT count(*) FROM decisions").fetchone() == (0,)

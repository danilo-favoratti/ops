"""Graph pagination preserves accounting rows and conservative identities."""

import base64
import json
from collections.abc import Iterator
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.graph_repository import DuckDBGraphRepository
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.repository import DuckDBExpenseRepository
from opsiu.application.graph import GraphFilters, GraphSearch, GraphService
from opsiu.application.ports import ExpenseSourceMetadata
from opsiu.domain.graph import GraphSnapshotError
from opsiu.domain.models import Despesa

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=UTC)
CNPJ = "11222333000181"
CPF = "52998224725"


def identity(number: int) -> str:
    return f"00000000-0000-7000-8000-{number:012d}"


def expense(
    *,
    document: int = 101,
    deputy: int = 7,
    name: str = "João da Silva",
    supplier: str | None = "São Comércio",
    fiscal_id: str | None = CNPJ,
    amount: str = "100.00",
    year: int = 2025,
    glosa: str | None = "0.00",
) -> Despesa:
    return Despesa.from_raw(
        {
            "nomeParlamentar": name,
            "siglaUF": "SP",
            "siglaPartido": "ABC",
            "numeroDeputadoID": deputy,
            "numeroSubCota": 1,
            "descricao": "Manutenção de escritório",
            "fornecedor": supplier,
            "cnpjCPF": fiscal_id,
            "numero": "NF-123",
            "dataEmissao": f"{year}-10-01",
            "valorDocumento": amount,
            "valorGlosa": glosa,
            "valorLiquido": amount,
            "ano": year,
            "mes": 10,
            "idDocumento": document,
        }
    )


def source(
    connection: DuckDBPyConnection,
    expenses: tuple[Despesa, ...],
    *,
    number: int,
    year: int = 2025,
) -> None:
    with DuckDBExpenseRepository(connection).stage(
        source_file_id=identity(number),
        source_year=year,
        run_id=identity(1),
        step_id=identity(2),
    ) as stage:
        stage.add_batch(expenses)
        stage.publish(
            ExpenseSourceMetadata(f"source-{number}.json", f"{number:064x}", 1234),
            ingested_at=NOW + timedelta(days=number),
        )


@pytest.fixture
def database() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect() as connection:
        apply_migrations(connection)
        connection.execute(
            "INSERT INTO runs VALUES (?, 'ingest', '{}', ?, 'commit', "
            "false, ?, NULL, 'running', '{}')",
            [identity(1), "a" * 64, NOW],
        )
        connection.execute(
            "INSERT INTO steps VALUES (?, ?, 'ingest', ?, NULL, 'running')",
            [identity(2), identity(1), NOW],
        )
        source(connection, (expense(amount="999999.99"),), number=10)
        source(
            connection,
            (
                expense(),
                expense(deputy=8, name="Maria Souza", amount="-25.00"),
                expense(document=0, fiscal_id=CPF, amount="0.00", glosa=None),
                expense(supplier="Alfa", fiscal_id="00000000000000", amount="12.00"),
                expense(supplier="Beta", fiscal_id="00000000000000", amount="13.00"),
                expense(supplier="Alfa", fiscal_id="00000000000000", amount="14.00"),
                expense(supplier=None, fiscal_id=None, amount="14.50"),
                expense(),  # Identical source records must remain separate nodes.
            ),
            number=20,
        )
        source(
            connection,
            (expense(year=2024, amount="1.01"),),
            number=30,
            year=2024,
        )
        yield connection


def test_complete_keyset_traversal_preserves_shared_and_missing_document_codes(
    database: DuckDBPyConnection,
) -> None:
    service = GraphService(DuckDBGraphRepository(database))
    filters = GraphFilters(limit=3)
    receipts: list[str] = []
    edge_ids: list[str] = []
    snapshots: set[str] = set()
    for _ in range(4):
        page = service.page(filters)
        assert page.page.returned <= 3
        assert len(page.nodes) <= 9
        assert len(page.edges) == page.page.returned * 2
        assert page.summary.reimbursements == 9
        assert page.summary.parliamentarians == 2
        assert page.summary.suppliers == 6
        assert page.summary.total_amount == "229.51"
        snapshots.add(page.snapshot)
        receipt_nodes = [node for node in page.nodes if node.kind == "reimbursement"]
        receipts.extend(node.id for node in receipt_nodes)
        edge_ids.extend(edge.id for edge in page.edges)
        # Every endpoint is present in its bounded response.
        node_ids = {node.id for node in page.nodes}
        assert all(
            edge.source in node_ids and edge.target in node_ids for edge in page.edges
        )
        assert page == service.page(filters)
        json.dumps(asdict(page), allow_nan=False)
        if not page.page.has_more:
            assert page.page.next_cursor is None
            break
        assert page.page.next_cursor is not None
        filters = replace(filters, cursor=page.page.next_cursor)
    else:
        pytest.fail("bounded graph traversal did not terminate")
    assert len(receipts) == len(set(receipts)) == 9
    assert len(edge_ids) == len(set(edge_ids)) == 18
    assert len(snapshots) == 1
    assert f"reimbursement:{identity(20)}:1" in receipts
    assert f"reimbursement:{identity(20)}:8" in receipts
    assert not any(identity(10) in value for value in receipts)


def test_signed_values_unknown_glosa_and_provenance_remain_exact(
    database: DuckDBPyConnection,
) -> None:
    page = DuckDBGraphRepository(database).page(GraphFilters(limit=100))
    receipts = {
        node.details["source_record_number"]: node
        for node in page.nodes
        if node.kind == "reimbursement"
        and node.details["source_file_id"] == identity(20)
    }
    assert receipts[2].details["amount"] == "-25.00"
    assert receipts[3].details["amount"] == "0.00"
    assert receipts[3].details["disallowed_amount"] is None
    assert receipts[3].label == "Documento sem código"
    assert receipts[1].details["document_id"] == receipts[8].details["document_id"]
    assert receipts[1].details["record_sha256"] == receipts[8].details["record_sha256"]
    assert receipts[1].id != receipts[8].id
    assert receipts[1].details["source_file"] == "source-20.json"
    assert receipts[1].details["source_sha256"] == f"{20:064x}"
    assert receipts[1].details["date"] == "2025-10-01"
    assert receipts[1].details["category"] == "Manutenção de escritório"


def test_valid_cnpj_and_cpf_merge_but_invalid_identities_are_row_scoped(
    database: DuckDBPyConnection,
) -> None:
    repository = DuckDBGraphRepository(database)
    page = repository.page(GraphFilters(limit=100))
    suppliers = [node for node in page.nodes if node.kind == "supplier"]
    assert {f"supplier:{CNPJ}", f"supplier:{CPF}"} <= {node.id for node in suppliers}
    isolated = [node for node in suppliers if ":unverified:" in node.id]
    assert len(isolated) == 4
    assert len({node.id for node in isolated}) == 4
    assert sum(node.label == "Alfa" for node in isolated) == 2
    for node in isolated:
        assert "isolado por registro" in str(node.details["identity_note"])
        selected = repository.page(GraphFilters(supplier_id=node.id))
        assert selected.summary.reimbursements == 1
    canonical = repository.page(GraphFilters(supplier_id=f"supplier:{CNPJ}"))
    assert canonical.summary.reimbursements == 4
    assert canonical.summary.total_amount == "176.01"


def test_combined_filters_and_accent_insensitive_search(
    database: DuckDBPyConnection,
) -> None:
    repository = DuckDBGraphRepository(database)
    page = repository.page(
        GraphFilters(deputy_id=7, supplier_id=f"supplier:{CNPJ}", year=2025)
    )
    assert page.summary.reimbursements == 2
    assert page.summary.total_amount == "200.00"
    assert repository.page(GraphFilters(query="maria")).summary.reimbursements == 1
    assert repository.page(GraphFilters(query="JOAO")).summary.reimbursements == 8
    assert repository.page(GraphFilters(query="escritorio")).summary.reimbursements == 9
    assert (
        repository.page(GraphFilters(query="11.222.333/0001-81")).summary.reimbursements
        == 4
    )
    # A global numeric query also searches fiscal IDs; the deputy filter is exact.
    assert repository.page(GraphFilters(query="8")).summary.reimbursements == 5
    assert repository.page(GraphFilters(deputy_id=8)).summary.reimbursements == 1
    assert repository.page(GraphFilters(year=2023)).summary.total_amount == "0.00"
    assert (
        repository.page(GraphFilters(query="' OR TRUE --")).summary.reimbursements == 0
    )
    assert database.execute("SELECT count(*) FROM despesas").fetchone() == (10,)

    deputies = repository.search(GraphSearch("parliamentarian", "JoAo"))
    assert [item.id for item in deputies.items] == ["deputy:7"]
    assert (
        repository.search(GraphSearch("parliamentarian", "8")).items[0].label
        == "Maria Souza"
    )
    suppliers = repository.search(GraphSearch("supplier", "sao"))
    assert {item.id for item in suppliers.items} == {
        f"supplier:{CNPJ}",
        f"supplier:{CPF}",
    }
    assert (
        repository.search(GraphSearch("supplier", "529.982.247-25")).items[0].id
        == f"supplier:{CPF}"
    )
    assert repository.search(GraphSearch("supplier", year=2023)).items == ()
    limited = repository.search(GraphSearch("supplier", limit=2))
    assert len(limited.items) == 2 and limited.has_more
    unknown = repository.search(GraphSearch("supplier", "Alfa"))
    assert len(unknown.items) == 2
    assert all("não confirmada" in item.subtitle for item in unknown.items)


@pytest.mark.parametrize(
    "fiscal_id,canonical",
    [
        (CNPJ, True),
        (CPF, True),
        ("11222333000180", False),
        ("52998224724", False),
        ("00000000000", False),
        ("11111111111111", False),
        (None, False),
    ],
)
def test_supplier_identity_requires_both_numeric_check_digits(
    database: DuckDBPyConnection, fiscal_id: str | None, canonical: bool
) -> None:
    source(
        database,
        (expense(fiscal_id=fiscal_id), expense(fiscal_id=fiscal_id)),
        number=40,
    )
    page = DuckDBGraphRepository(database).page(GraphFilters(year=2025))
    suppliers = [node for node in page.nodes if node.kind == "supplier"]
    assert len(suppliers) == (1 if canonical else 2)
    assert all(
        (node.id == f"supplier:{fiscal_id}")
        if canonical
        else node.id.startswith("supplier:unverified:")
        for node in suppliers
    )


@pytest.mark.parametrize("query", ["%", "---", "🔎", "漢字"])
def test_search_without_searchable_characters_does_not_match_every_record(
    database: DuckDBPyConnection, query: str
) -> None:
    repository = DuckDBGraphRepository(database)
    assert repository.page(GraphFilters(query=query)).summary.reimbursements == 0
    assert repository.search(GraphSearch("supplier", query)).items == ()
    assert repository.search(GraphSearch("parliamentarian", query)).items == ()


def test_negative_document_sentinel_keeps_its_provenance_but_has_no_code_label(
    database: DuckDBPyConnection,
) -> None:
    source(database, (expense(document=-1),), number=40)
    page = DuckDBGraphRepository(database).page(GraphFilters(year=2025))
    receipts = [node for node in page.nodes if node.kind == "reimbursement"]
    assert len(receipts) == 1
    assert receipts[0].label == "Documento sem código"
    assert receipts[0].details["document_id"] == -1


def test_cursor_rejects_changed_filters_and_replaced_empty_source(
    database: DuckDBPyConnection,
) -> None:
    repository = DuckDBGraphRepository(database)
    filters = GraphFilters(limit=2)
    first = repository.page(filters)
    assert first.page.next_cursor is not None
    with pytest.raises(ValueError, match="incompatível"):
        repository.page(replace(filters, cursor=first.page.next_cursor, deputy_id=7))
    with pytest.raises(ValueError, match="incompatível"):
        repository.page(replace(filters, cursor=first.page.next_cursor, limit=3))
    source(database, (), number=40)
    with pytest.raises(GraphSnapshotError, match="atualizados"):
        repository.page(replace(filters, cursor=first.page.next_cursor))
    assert repository.page(filters).summary.reimbursements == 1


@pytest.mark.parametrize("cursor", ["!", "AAAA", "e30=", "W10=", "bnVsbA=="])
def test_invalid_cursors_cannot_select_or_mutate_arbitrary_rows(
    database: DuckDBPyConnection, cursor: str
) -> None:
    with pytest.raises(ValueError, match="Cursor"):
        DuckDBGraphRepository(database).page(GraphFilters(cursor=cursor))
    assert database.execute("SELECT count(*) FROM despesas").fetchone() == (10,)


@pytest.mark.parametrize(
    "key,value",
    [("v", True), ("row", True), ("row", -1), ("source", "'; DROP TABLE despesas;--")],
)
def test_invalid_decoded_cursor_types_are_rejected(
    database: DuckDBPyConnection, key: str, value: object
) -> None:
    repository = DuckDBGraphRepository(database)
    page = repository.page(GraphFilters(limit=2))
    assert page.page.next_cursor is not None
    payload = json.loads(base64.urlsafe_b64decode(page.page.next_cursor))
    payload[key] = value
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    with pytest.raises(ValueError, match="Cursor"):
        repository.page(GraphFilters(limit=2, cursor=cursor))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"year": 0},
        {"year": 10000},
        {"year": True},
        {"deputy_id": "7"},
        {"deputy_id": 2**63},
        {"deputy_id": True},
        {"supplier_id": "supplier:' OR TRUE"},
        {"supplier_id": "supplier:abc"},
        {"query": "a" * 121},
        {"query": "abc\x00def"},
        {"cursor": ""},
        {"cursor": "a" * 2049},
    ],
)
def test_graph_filters_reject_unbounded_or_malformed_input(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        GraphFilters(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "reimbursement"},
        {"kind": "supplier", "limit": 21},
        {"kind": "supplier", "limit": 0},
        {"kind": "supplier", "year": True},
        {"kind": "supplier", "q": "a" * 121},
    ],
)
def test_graph_search_rejects_unbounded_or_malformed_input(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        GraphSearch(**kwargs)


def test_empty_source_snapshot_produces_an_empty_serializable_graph(
    database: DuckDBPyConnection,
) -> None:
    source(database, (), number=40)
    source(database, (), number=50, year=2024)
    page = DuckDBGraphRepository(database).page(GraphFilters())
    assert page.nodes == ()
    assert page.edges == ()
    assert not page.page.has_more and page.page.next_cursor is None
    assert page.page.returned == 0
    assert page.summary.reimbursements == 0
    assert Decimal(page.summary.total_amount) == Decimal("0.00")
    json.dumps(asdict(page))

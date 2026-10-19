"""Election-window boundaries, matching priority, and missing-data failures."""

from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from decimal import Decimal

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.check_repository import DuckDBCheckRepository
from opsiu.domain.errors import DomainError
from opsiu.domain.policies import ELECTION_DATES
from opsiu.domain.rules.r11_divulgacao_eleitoral import R11DivulgacaoEleitoral

pytestmark = pytest.mark.unit


@pytest.fixture
def database() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE despesas_current(id_documento BIGINT, "
            "valor_liquido DECIMAL(18,2), data_emissao DATE, numero_sub_cota INTEGER, "
            "cpf VARCHAR, nome_parlamentar VARCHAR, sigla_uf VARCHAR); "
            "CREATE TABLE tse_imports_current(year INTEGER, snapshot_id VARCHAR, "
            "source_sha256 VARCHAR); "
            "CREATE TABLE tse_candidatos_current(year INTEGER, candidate_id VARCHAR, "
            "cpf VARCHAR, nome_normalizado VARCHAR, uf VARCHAR, cargo VARCHAR, "
            "source_member VARCHAR, source_record_number BIGINT)"
        )
        yield connection


def add_candidate(
    database: DuckDBPyConnection,
    *,
    identity: str = "1",
    cpf: str | None = "01234567890",
    name: str = "JOSE DA SILVA",
    state: str = "SP",
    year: int = 2024,
) -> None:
    database.execute(
        "INSERT INTO tse_candidatos_current VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [year, identity, cpf, name, state, "PREFEITO", "payload", int(identity)],
    )


def add_expense(
    database: DuckDBPyConnection,
    *,
    document: int = 1,
    issued: date = date(2024, 9, 1),
    cpf: str | None = "01234567890",
    name: str = "José da Silva",
    state: str = "SP",
    category: int = 5,
) -> None:
    database.execute(
        "INSERT INTO despesas_current VALUES (?, ?, ?, ?, ?, ?, ?)",
        [document, Decimal("12.34"), issued, category, cpf, name, state],
    )


def rows(database: DuckDBPyConnection) -> tuple[Mapping[str, object], ...]:
    return tuple(DuckDBCheckRepository(database).candidates(R11DivulgacaoEleitoral.sql))


@pytest.mark.parametrize("year", [2024, 2026])
def test_exact_120_day_window_excludes_election_day_and_other_categories(
    database: DuckDBPyConnection, year: int
) -> None:
    database.execute(
        "INSERT INTO tse_imports_current VALUES (?, 'snapshot', 'hash')", [year]
    )
    add_candidate(database, year=year)
    for document, offset in enumerate((-121, -120, -1, 0, 1), start=1):
        add_expense(
            database,
            document=document,
            issued=ELECTION_DATES[year] + timedelta(days=offset),
        )
    add_expense(
        database,
        document=6,
        issued=ELECTION_DATES[year] - timedelta(days=1),
        category=1,
    )
    results = [R11DivulgacaoEleitoral().evaluate(row) for row in rows(database)]
    assert [result.id_documento for result in results if result] == [2, 3]
    assert all(result and result.amount == Decimal("12.34") for result in results)


def test_exact_cpf_takes_priority_over_ambiguous_name_fallback(
    database: DuckDBPyConnection,
) -> None:
    database.execute("INSERT INTO tse_imports_current VALUES (2024, 's', 'h')")
    add_candidate(database, name="OTHER NAME", state="RJ")
    add_candidate(database, identity="2", cpf=None)
    add_candidate(database, identity="3", cpf=None)
    add_expense(database)
    result = R11DivulgacaoEleitoral().evaluate(rows(database)[0])
    assert result is not None
    assert result.evidence["metodo_correspondencia"] == "cpf"
    candidates = result.evidence["candidatos"]
    assert isinstance(candidates, list) and len(candidates) == 1


def test_missing_cpf_name_match_is_ambiguous_for_distinct_identities(
    database: DuckDBPyConnection,
) -> None:
    database.execute("INSERT INTO tse_imports_current VALUES (2024, 's', 'h')")
    add_candidate(database)
    add_candidate(database, identity="2", cpf="98765432100")
    add_expense(database, cpf=None)
    with pytest.raises(DomainError, match="ambiguous"):
        R11DivulgacaoEleitoral().evaluate(rows(database)[0])


def test_missing_successful_import_differs_from_successful_empty_source(
    database: DuckDBPyConnection,
) -> None:
    add_expense(database)
    with pytest.raises(DomainError, match="successful TSE import"):
        R11DivulgacaoEleitoral().evaluate(rows(database)[0])
    database.execute("INSERT INTO tse_imports_current VALUES (2024, 's', 'h')")
    assert R11DivulgacaoEleitoral().evaluate(rows(database)[0]) is None


def test_known_different_cpfs_never_fall_back_and_same_cpf_can_have_two_records(
    database: DuckDBPyConnection,
) -> None:
    database.execute("INSERT INTO tse_imports_current VALUES (2024, 's', 'h')")
    add_candidate(database)
    add_expense(database, cpf="98765432100")
    assert R11DivulgacaoEleitoral().evaluate(rows(database)[0]) is None
    add_candidate(database, identity="2", name="ANOTHER REGISTERED NAME")
    add_expense(database, document=2)
    result = R11DivulgacaoEleitoral().evaluate(rows(database)[1])
    assert result is not None
    candidates = result.evidence["candidatos"]
    assert isinstance(candidates, list) and len(candidates) == 2

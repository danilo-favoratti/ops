"""Payee exceptions, distinct invoice groups and exact calendar-day boundaries."""

from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.domain.errors import DomainError
from opsiu.domain.invoice_numbers import usable_invoice_number
from opsiu.domain.rules.base import Rule
from opsiu.domain.rules.r08_pagamento_cpf import R08PagamentoCPF
from opsiu.domain.rules.r09_nota_duplicada import R09NotaDuplicada
from opsiu.domain.rules.r10_prazo_reembolso import R10PrazoReembolso

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE despesas_current(id_documento BIGINT, "
            "numero_deputado_id BIGINT, "
            "payer_kind VARCHAR, cnpj_cpf_digits VARCHAR, numero_sub_cota INTEGER, "
            "descricao VARCHAR, numero VARCHAR, valor_documento DECIMAL(18,2), "
            "valor_liquido DECIMAL(18,2), data_emissao DATE, ano INTEGER, mes INTEGER)"
        )
        yield connection


def candidates(db: DuckDBPyConnection, rule: Rule) -> list[Mapping[str, object]]:
    query = db.execute(rule.sql)
    names = [column[0] for column in query.description]
    return [dict(zip(names, values, strict=True)) for values in query.fetchall()]


@pytest.mark.parametrize(
    "payer,category,expected",
    [
        ("CPF", 3, True),
        ("CPF", 120, True),
        ("CPF", 8, True),
        ("CPF", 1, False),
        ("CPF", 119, False),
        ("CPF", 121, False),
        ("CPF", 122, False),
        ("CNPJ", 3, False),
        ("OUTRO", 3, False),
    ],
)
def test_cpf_rule_respects_each_category_exception_and_payer_kind(
    db: DuckDBPyConnection,
    payer: str,
    category: int,
    expected: bool,
) -> None:
    db.execute(
        "INSERT INTO despesas_current (id_documento, payer_kind, cnpj_cpf_digits, "
        "numero_sub_cota, descricao, valor_documento, valor_liquido) "
        "VALUES (1, ?, '00000000001', ?, 'Categoria sintética', 30.00, 25.00)",
        [payer, category],
    )
    rule = R08PagamentoCPF()
    rows = candidates(db, rule)
    assert bool(rows) == expected
    if rows:
        result = rule.evaluate(rows[0])
        assert result is not None and result.amount == Decimal("25.00")
        assert result.evidence["numero_sub_cota"] == category
        assert result.evidence["cnpj_cpf_digits"] == "00000000001"


def test_duplicates_use_gross_value_across_deputies_and_distinct_sorted_ids(
    db: DuckDBPyConnection,
) -> None:
    db.execute(
        "INSERT INTO despesas_current (id_documento, numero_deputado_id, "
        "cnpj_cpf_digits, "
        "numero, valor_documento, valor_liquido, data_emissao) VALUES "
        "(2, 200, '00000000000001', '000001', 321.09, 320.09, '2099-01-01'), "
        "(1, 100, '00000000000001', '000001', 321.09, 321.09, '2099-01-01'), "
        "(3, 100, '00000000000001', '000002', 321.09, 321.09, '2099-01-01'), "
        "(4, 100, '00000000000001', '000001', 321.08, 321.08, '2099-01-01'), "
        "(5, 100, '00000000000002', '000001', 321.09, 321.09, '2099-01-01'), "
        "(6, 100, '00000000000001', '1', 321.09, 321.09, '2099-01-01')"
    )
    rule = R09NotaDuplicada()
    rows = candidates(db, rule)
    assert [row["id_documento"] for row in rows] == [1, 2]
    for row in rows:
        result = rule.evaluate(row)
        assert result is not None and result.amount == Decimal("321.09")
        assert result.evidence["documentos"] == [1, 2]
        assert result.evidence["numero"] == "000001"
        assert result.evidence["data_emissao"] == date(2099, 1, 1)
        assert result.evidence["possivel_duplicidade"] is True


def test_repeated_same_document_does_not_create_a_duplicate_group(
    db: DuckDBPyConnection,
) -> None:
    db.execute(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, "
        "numero, valor_documento, data_emissao) "
        "VALUES (1, '00000000000001', 'A', 10, '2099-01-01'), "
        "(1, '00000000000001', 'A', 10, '2099-01-01')"
    )
    rule = R09NotaDuplicada()
    assert candidates(db, rule) == []
    db.execute(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, "
        "numero, valor_documento, data_emissao) "
        "VALUES (2, '00000000000001', 'A', 10, '2099-01-01')"
    )
    rows = candidates(db, rule)
    assert [row["id_documento"] for row in rows] == [1, 2]
    assert all(row["documentos"] == [1, 2] for row in rows)


@pytest.mark.parametrize(
    "payee,number,gross",
    [
        (None, "A", "10"),
        ("", "A", "10"),
        ("  ", "A", "10"),
        ("00000000000001", None, "10"),
        ("00000000000001", "", "10"),
        ("00000000000001", "  ", "10"),
        ("00000000000001", "A", None),
    ],
)
def test_missing_duplicate_keys_cannot_prove_an_invoice_match(
    db: DuckDBPyConnection,
    payee: str | None,
    number: str | None,
    gross: str | None,
) -> None:
    db.executemany(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, "
        "numero, valor_documento, data_emissao) "
        "VALUES (?, ?, ?, ?, '2099-01-01')",
        [
            (document, payee, number, Decimal(gross) if gross is not None else None)
            for document in (1, 2)
        ],
    )
    assert candidates(db, R09NotaDuplicada()) == []


@pytest.mark.parametrize(
    "number,eligible",
    [
        (None, False),
        ("", False),
        (" / - . ", False),
        ("s/n", False),
        ("SN", False),
        ("s.n.º", False),
        ("s/no", False),
        ("N/D", False),
        ("Sem número", False),
        ("000000", False),
        ("0/0", False),
        ("000001", True),
        ("SN123", True),
        ("A-000", True),
        ("NA", True),
    ],
)
def test_duplicate_number_policy_excludes_markers_without_rewriting_real_numbers(
    db: DuckDBPyConnection, number: str | None, eligible: bool
) -> None:
    db.executemany(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, numero, "
        "valor_documento, data_emissao) VALUES (?, '00000000000001', ?, 10, "
        "'2099-01-01')",
        [(document, number) for document in (1, 2)],
    )
    rule = R09NotaDuplicada()
    rows = candidates(db, rule)
    assert bool(rows) is eligible
    assert usable_invoice_number(number) is eligible
    direct = rule.evaluate(
        {
            "id_documento": 1,
            "documentos": [1, 2],
            "cnpj_cpf_digits": "00000000000001",
            "numero": number,
            "valor_documento": Decimal("10.00"),
            "data_emissao": date(2099, 1, 1),
        }
    )
    assert (direct is not None) is eligible
    for row in rows:
        result = rule.evaluate(row)
        assert result is not None and result.evidence["numero"] == number


@pytest.mark.parametrize("issued", [None, date(2099, 1, 2)])
def test_duplicate_metadata_requires_same_known_issue_date(
    db: DuckDBPyConnection, issued: date | None
) -> None:
    db.executemany(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, numero, "
        "valor_documento, data_emissao) VALUES (?, '00000000000001', '123', 10, ?)",
        [(1, date(2099, 1, 1)), (2, issued)],
    )
    assert candidates(db, R09NotaDuplicada()) == []


def test_nonpositive_document_ids_do_not_form_metadata_duplicate_groups(
    db: DuckDBPyConnection,
) -> None:
    db.executemany(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, numero, "
        "valor_documento, data_emissao) VALUES (?, '00000000000001', '123', 10, "
        "'2099-01-01')",
        [(document,) for document in (-1, 0, 1)],
    )
    assert candidates(db, R09NotaDuplicada()) == []


def test_receipt_type_does_not_hide_an_otherwise_matching_duplicate(
    db: DuckDBPyConnection,
) -> None:
    db.execute("ALTER TABLE despesas_current ADD COLUMN tipo_documento INTEGER")
    db.executemany(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, numero, "
        "valor_documento, data_emissao, tipo_documento) "
        "VALUES (?, '00000000000001', '123', 10, '2099-01-01', ?)",
        [(1, 0), (2, 1)],
    )
    assert len(candidates(db, R09NotaDuplicada())) == 2


@pytest.mark.parametrize(
    "year,month,issued,days",
    [
        (2099, 7, date(2099, 4, 1), 91),
        (2099, 7, date(2099, 4, 2), 90),
        (2099, 7, date(2099, 4, 3), 89),
        (2024, 5, date(2024, 2, 1), 90),
        (2024, 5, date(2024, 1, 31), 91),
        (2023, 5, date(2023, 1, 31), 90),
        (2024, 1, date(2023, 10, 2), 91),
        (2024, 1, date(2023, 10, 3), 90),
        (2099, 7, date(2099, 7, 10), -9),
        (2099, 7, None, None),
    ],
)
def test_accounting_dates_never_substitute_missing_submission_date(
    db: DuckDBPyConnection,
    year: int,
    month: int,
    issued: date | None,
    days: int | None,
) -> None:
    db.execute(
        "INSERT INTO despesas_current (id_documento, data_emissao, ano, mes, "
        "valor_documento, valor_liquido) VALUES (1, ?, ?, ?, 20.00, 10.00)",
        [issued, year, month],
    )
    rule = R10PrazoReembolso()
    rows = candidates(db, rule)
    assert rows == []
    assert rule.descriptor.suspended_reason is not None
    with pytest.raises(DomainError, match="suspended rule"):
        rule.evaluate(
            {
                "id_documento": 1,
                "data_emissao": issued,
                "competencia": date(year, month, 1),
                "dias_decorridos": days,
                "valor_liquido": Decimal("10.00"),
            }
        )

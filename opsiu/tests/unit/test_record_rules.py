"""Payee exceptions, distinct invoice groups and exact calendar-day boundaries."""

from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal

import duckdb
import pytest
from duckdb import DuckDBPyConnection

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
        "numero, valor_documento, valor_liquido) VALUES "
        "(2, 200, '00000000000001', '000001', 321.09, 320.09), "
        "(1, 100, '00000000000001', '000001', 321.09, 321.09), "
        "(3, 100, '00000000000001', '000002', 321.09, 321.09), "
        "(4, 100, '00000000000001', '000001', 321.08, 321.08), "
        "(5, 100, '00000000000002', '000001', 321.09, 321.09), "
        "(6, 100, '00000000000001', '1', 321.09, 321.09)"
    )
    rule = R09NotaDuplicada()
    rows = candidates(db, rule)
    assert [row["id_documento"] for row in rows] == [1, 2]
    for row in rows:
        result = rule.evaluate(row)
        assert result is not None and result.amount == Decimal("321.09")
        assert result.evidence["documentos"] == [1, 2]
        assert result.evidence["numero"] == "000001"
        assert result.evidence["possivel_duplicidade"] is True


def test_repeated_same_document_does_not_create_a_duplicate_group(
    db: DuckDBPyConnection,
) -> None:
    db.execute(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, "
        "numero, valor_documento) "
        "VALUES (1, '00000000000001', 'A', 10), (1, '00000000000001', 'A', 10)"
    )
    rule = R09NotaDuplicada()
    assert candidates(db, rule) == []
    db.execute(
        "INSERT INTO despesas_current (id_documento, cnpj_cpf_digits, "
        "numero, valor_documento) "
        "VALUES (2, '00000000000001', 'A', 10)"
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
        "numero, valor_documento) "
        "VALUES (?, ?, ?, ?)",
        [
            (document, payee, number, Decimal(gross) if gross is not None else None)
            for document in (1, 2)
        ],
    )
    assert candidates(db, R09NotaDuplicada()) == []


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
def test_deadline_strict_boundary_handles_leap_years_cross_years_and_missing_dates(
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
    assert bool(rows) == (days is not None and days > 90)
    direct = rule.evaluate(
        {
            "id_documento": 1,
            "data_emissao": issued,
            "competencia": date(year, month, 1),
            "valor_liquido": Decimal("10.00"),
        }
    )
    assert (direct is not None) == bool(rows)
    if rows:
        result = rule.evaluate(rows[0])
        assert result is not None and result.amount == Decimal("10.00")
        assert result.evidence == {
            "data_emissao": issued,
            "competencia": date(year, month, 1),
            "dias_decorridos": days,
            "prazo_dias": 90,
            "competencia_e_proxy": True,
        }

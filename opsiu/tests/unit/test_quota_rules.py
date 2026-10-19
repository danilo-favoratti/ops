"""Cumulative quotas preserve calendar, historical policy and role boundaries."""

from collections.abc import Iterator, Mapping
from decimal import Decimal

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.domain.errors import DomainError
from opsiu.domain.rules.base import Rule
from opsiu.domain.rules.quota import LEADERSHIP_ROLES
from opsiu.domain.rules.r07_cota_uf import R07CotaUF
from opsiu.domain.rules.r14_liderancas import R14Liderancas

pytestmark = pytest.mark.unit


@pytest.fixture
def db() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE despesas_current(numero_deputado_id BIGINT, ano INTEGER, "
            "mes INTEGER, valor_liquido DECIMAL(18,2), id_documento BIGINT, "
            "cpf VARCHAR, sigla_uf VARCHAR, nome_parlamentar VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE cota_uf(uf VARCHAR, value DECIMAL(18,2), valid_from DATE)"
        )
        connection.execute(
            "CREATE TABLE adicionais(role VARCHAR, value DECIMAL(18,2), "
            "valid_from DATE)"
        )
        connection.execute(
            "INSERT INTO cota_uf VALUES ('DF',100,DATE '2098-01-01'), "
            "('SP',200,DATE '2098-01-01')"
        )
        yield connection


def expense(
    db: DuckDBPyConnection,
    year: int,
    month: int,
    value: str,
    *,
    document: int | None = None,
    uf: str = "DF",
    name: str = "Deputada Sintética",
    leadership: bool = False,
) -> None:
    db.execute(
        "INSERT INTO despesas_current VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
        [
            year,
            month,
            Decimal(value),
            document if document is not None else year * 100 + month,
            None if leadership else "00000000001",
            "NA" if leadership else uf,
            name,
        ],
    )


def rows(db: DuckDBPyConnection, rule: Rule) -> list[Mapping[str, object]]:
    query = db.execute(rule.sql)
    names = [column[0] for column in query.description]
    return [dict(zip(names, values, strict=True)) for values in query.fetchall()]


def test_gap_month_accrues_quota_without_a_candidate_or_reused_document(
    db: DuckDBPyConnection,
) -> None:
    expense(db, 2099, 1, "10")
    expense(db, 2099, 3, "310")
    rule = R07CotaUF()
    candidates = rows(db, rule)
    assert [row["mes"] for row in candidates] == [1, 3]
    assert rule.evaluate(candidates[0]) is None
    finding = rule.evaluate(candidates[1])
    assert finding is not None and finding.id_documento == 209903
    assert finding.amount == Decimal("20.00")
    assert finding.evidence["limite_acumulado"] == Decimal("300.00")
    assert finding.evidence["disponivel_no_mes"] == Decimal("290.00")
    assert finding.evidence["limite_com_margem"] == Decimal("315.0000")
    assert finding.evidence["approximate"] is True
    series = finding.evidence["serie_mensal"]
    assert isinstance(series, list) and len(series) == 3
    assert series[1]["mes"] == 2 and series[1]["gasto"] == Decimal("0")
    assert series[1]["cota"] == Decimal("100")


def test_large_month_can_use_prior_balance_but_new_year_cannot(
    db: DuckDBPyConnection,
) -> None:
    expense(db, 2099, 1, "10")
    expense(db, 2099, 2, "180")
    expense(db, 2100, 1, "150")
    rule = R07CotaUF()
    outcomes = [rule.evaluate(row) for row in rows(db, rule)]
    assert outcomes[:2] == [None, None]
    finding = outcomes[2]
    assert finding is not None
    assert finding.id_documento == 210001 and finding.amount == Decimal("50.00")
    assert finding.evidence["limite_acumulado"] == Decimal("100.00")


def test_first_expense_month_uses_sum_of_each_months_dated_allowance(
    db: DuckDBPyConnection,
) -> None:
    db.execute(
        "INSERT INTO cota_uf VALUES ('DF',150,DATE '2099-02-01'), "
        "('DF',200,DATE '2099-02-15'), ('DF',999,DATE '2100-01-01')"
    )
    expense(db, 2099, 3, "451")
    rule = R07CotaUF(Decimal("0"))
    candidates = rows(db, rule)
    assert len(candidates) == 1
    result = rule.evaluate(candidates[0])
    assert result is not None and result.amount == Decimal("1.00")
    assert result.evidence["limite_acumulado"] == Decimal("450.00")
    series = result.evidence["serie_mensal"]
    assert isinstance(series, list)
    assert [month["cota"] for month in series] == [
        Decimal("100"),
        Decimal("150"),
        Decimal("200"),
    ]


@pytest.mark.parametrize(
    "value,margin,expected",
    [
        ("105.00", "5", None),
        ("105.01", "5", "5.01"),
        ("100.01", "0", "0.01"),
        ("102.50", "2.5", None),
    ],
)
def test_margin_is_a_strict_cumulative_filter_not_part_of_excess(
    db: DuckDBPyConnection,
    value: str,
    margin: str,
    expected: str | None,
) -> None:
    expense(db, 2099, 1, value)
    rule = R07CotaUF(Decimal(margin))
    result = rule.evaluate(rows(db, rule)[0])
    assert (result.amount if result else None) == (
        Decimal(expected) if expected else None
    )


def test_fractional_cent_threshold_is_not_rounded_before_comparison(
    db: DuckDBPyConnection,
) -> None:
    db.execute("INSERT INTO cota_uf VALUES ('DF',100.01,DATE '2099-01-01')")
    expense(db, 2099, 1, "105.01")
    rule = R07CotaUF()
    assert rule.evaluate(rows(db, rule)[0]) is None


def test_state_is_historical_per_year_and_conflicting_same_year_fails(
    db: DuckDBPyConnection,
) -> None:
    expense(db, 2099, 1, "110", uf="DF")
    expense(db, 2100, 1, "110", uf="SP")
    rule = R07CotaUF()
    candidates = rows(db, rule)
    first = rule.evaluate(candidates[0])
    assert first is not None and first.amount == Decimal("10")
    assert rule.evaluate(candidates[1]) is None
    expense(db, 2099, 2, "10", uf="SP")
    with pytest.raises(DomainError, match="ambiguous state"):
        rule.evaluate(rows(db, rule)[0])


def test_missing_policy_in_zero_spend_month_is_not_silently_ignored(
    db: DuckDBPyConnection,
) -> None:
    db.execute("DELETE FROM cota_uf")
    db.execute("INSERT INTO cota_uf VALUES ('DF',100,DATE '2099-02-01')")
    expense(db, 2099, 3, "301")
    rule = R07CotaUF()
    candidates = rows(db, rule)
    assert len(candidates) == 1
    with pytest.raises(DomainError, match="no quota policy"):
        rule.evaluate(candidates[0])


@pytest.mark.parametrize("name,role", LEADERSHIP_ROLES.items())
def test_known_leadership_alias_resolves_one_non_cumulative_addition(
    db: DuckDBPyConnection,
    name: str,
    role: str,
) -> None:
    db.execute(
        "INSERT INTO adicionais VALUES (?,100,DATE '2098-01-01'), "
        "('unrelated_role',1000,DATE '2098-01-01')",
        [role],
    )
    expense(db, 2099, 1, "110", name=name, leadership=True)
    rule = R14Liderancas()
    result = rule.evaluate(rows(db, rule)[0])
    assert result is not None and result.amount == Decimal("10.00")
    assert result.evidence["cargo"] == role
    assert result.evidence["cota_mensal"] == Decimal("0")
    assert result.evidence["adicional_mensal"] == Decimal("100")
    assert rows(db, R07CotaUF()) == []


def test_leadership_uses_dated_additions_and_unknown_names_fail(
    db: DuckDBPyConnection,
) -> None:
    db.execute(
        "INSERT INTO adicionais VALUES ('lider_governo_camara',100,DATE '2098-01-01'), "
        "('lider_governo_camara',200,DATE '2099-02-01')"
    )
    expense(db, 2099, 2, "310", name="LIDERANÇA DO GOVERNO NA CÂMARA", leadership=True)
    rule = R14Liderancas(Decimal("0"))
    result = rule.evaluate(rows(db, rule)[0])
    assert result is not None and result.amount == Decimal("10")
    db.execute("UPDATE despesas_current SET nome_parlamentar='LIDERANÇA DESCONHECIDA'")
    with pytest.raises(DomainError, match="unknown or ambiguous leadership role"):
        rule.evaluate(rows(db, rule)[0])


def test_multiple_roles_and_mixed_classifications_do_not_invent_an_allowance(
    db: DuckDBPyConnection,
) -> None:
    db.execute(
        "INSERT INTO adicionais VALUES ('lider_governo_camara',100,DATE '2098-01-01'), "
        "('vice_lider_minoria',50,DATE '2098-01-01')"
    )
    expense(db, 2099, 1, "100", name="LIDERANÇA DO GOVERNO NA CÂMARA", leadership=True)
    expense(db, 2099, 2, "100", name="VICE-LIDERANÇA DA MINORIA", leadership=True)
    rule = R14Liderancas()
    with pytest.raises(DomainError, match="unknown or ambiguous leadership role"):
        rule.evaluate(rows(db, rule)[0])
    expense(db, 2099, 3, "100")
    for variant in (R07CotaUF(), R14Liderancas()):
        with pytest.raises(DomainError, match="ambiguous state"):
            variant.evaluate(rows(db, variant)[0])

"""Dated legal caps, partial-month transitions, and unsupported policy periods."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from opsiu.domain.researched_caps import (
    COURSE_CAP_POLICIES,
    RENTAL_CAP_POLICIES,
    RENTAL_UPDATED_LIMIT,
    MonthlyCapPolicy,
)
from opsiu.domain.rules.r01_locacao_veiculos import R01LocacaoVeiculos
from opsiu.domain.rules.r05_cursos_eventos import R05CursosEventos
from opsiu.domain.rules.researched_monthly_cap import ResearchedMonthlyCapEvaluator

pytestmark = pytest.mark.unit


class HistoricalRentalRule(R01LocacaoVeiculos):
    """Explicit real policies, independent of synthetic pipeline fixtures."""

    policies = RENTAL_CAP_POLICIES


class HistoricalCourseRule(R05CursosEventos):
    policies = COURSE_CAP_POLICIES


def row(year: int, month: int, total: str, category: int) -> Mapping[str, object]:
    return {
        "numero_deputado_id": 101,
        "ano": year,
        "mes": month,
        "numero_sub_cota": category,
        "total": Decimal(total),
        "id_documento": 12,
        "documentos": [11, 12],
    }


@pytest.mark.parametrize(
    "rule,category,year,month,limit",
    [
        (HistoricalRentalRule(), 120, 2023, 1, "12713.00"),
        (HistoricalRentalRule(), 120, 2025, 4, "12713.00"),
        (HistoricalRentalRule(), 120, 2025, 6, "18970.42"),
        (HistoricalRentalRule(), 120, 2026, 2, "18970.42"),
        (HistoricalCourseRule(), 137, 2023, 1, "7697.17"),
        (HistoricalCourseRule(), 137, 2023, 2, "9145.62"),
        (HistoricalCourseRule(), 137, 2025, 12, "9145.62"),
        (HistoricalCourseRule(), 137, 2026, 1, "9145.62"),
        (HistoricalCourseRule(), 137, 2026, 3, "10403.14"),
    ],
)
def test_researched_caps_preserve_exact_cent_boundaries(
    rule: ResearchedMonthlyCapEvaluator,
    category: int,
    year: int,
    month: int,
    limit: str,
) -> None:
    assert rule.evaluate(row(year, month, limit, category)) is None
    assert (
        rule.evaluate(row(year, month, str(Decimal(limit) - Decimal("0.01")), category))
        is None
    )
    result = rule.evaluate(
        row(year, month, str(Decimal(limit) + Decimal("0.01")), category)
    )
    assert result is not None
    assert result.amount == Decimal("0.01")
    assert result.severity is None
    assert result.evidence["limite"] == Decimal(limit)
    assert result.evidence["documentos"] == [11, 12]
    assert result.evidence["politica_fontes"]
    assert result.evidence["politica_arredondamento"] == "ROUND_HALF_UP para centavos"


def test_rental_ipca_uses_month_before_start_and_known_effective_date() -> None:
    assert RENTAL_UPDATED_LIMIT == Decimal("18970.42")
    result = HistoricalRentalRule().evaluate(row(2025, 6, "19000.00", 120))
    assert result is not None
    assert result.evidence["vigente_desde"] == date(2025, 5, 28)
    assert result.evidence["politica_inicio_competencia"] == date(2025, 6, 1)
    assert "2017-04: 4828.44" in str(result.evidence["politica_formula"])


@pytest.mark.parametrize(
    "rule,category,year,month,total,limit,transition_limit",
    [
        (
            HistoricalRentalRule(),
            120,
            2025,
            5,
            "18000.00",
            "12713.00",
            "18970.42",
        ),
        (
            HistoricalCourseRule(),
            137,
            2026,
            2,
            "10000.00",
            "9145.62",
            "10403.14",
        ),
    ],
)
def test_partial_months_require_review_instead_of_old_cap_violation(
    rule: ResearchedMonthlyCapEvaluator,
    category: int,
    year: int,
    month: int,
    total: str,
    limit: str,
    transition_limit: str,
) -> None:
    result = rule.evaluate(row(year, month, total, category))
    assert result is not None
    assert result.severity == "revisar"
    assert result.evidence["limite"] == Decimal(limit)
    assert result.evidence["limite_transicao"] == Decimal(transition_limit)
    assert result.evidence["politica_limitacao"]


@pytest.mark.parametrize("month", [3, 4, 12])
def test_rental_additional_allowance_requires_actual_entitlement(month: int) -> None:
    rule = HistoricalRentalRule()
    assert rule.evaluate(row(2026, month, "18970.42", 120)) is None
    result = rule.evaluate(row(2026, month, "19000.00", 120))
    assert result is not None
    assert result.severity == "revisar"
    assert "autorização" in str(result.evidence["politica_limitacao"])
    assert result.evidence["limite_transicao"] is None


@pytest.mark.parametrize("rule", [HistoricalRentalRule(), HistoricalCourseRule()])
@pytest.mark.parametrize("year", [2022, 2027, 2099])
def test_unknown_policy_years_are_explicit_coverage_gaps(
    rule: ResearchedMonthlyCapEvaluator, year: int
) -> None:
    candidate = row(year, 1, "50000.00", 120)
    assert rule.coverage_reason(candidate)
    assert rule.evaluate(candidate) is None


def test_researched_ranges_cover_all_audit_months_without_overlap() -> None:
    for policies in (RENTAL_CAP_POLICIES, COURSE_CAP_POLICIES):
        assert policies[0].start == date(2023, 1, 1)
        assert policies[-1].end == date(2027, 1, 1)
        assert all(
            first.end == second.start
            for first, second in zip(policies, policies[1:], strict=False)
        )
        for policy in policies:
            with pytest.raises(FrozenInstanceError):
                setattr(policy, "limit", Decimal("1.00"))


def test_rental_sql_aggregates_refunds_without_using_legacy_policy_table() -> None:
    rule = HistoricalRentalRule()
    with duckdb.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE despesas_current (numero_deputado_id INTEGER, "
            "ano INTEGER, mes INTEGER, numero_sub_cota INTEGER, "
            "valor_liquido DECIMAL(18,2), id_documento INTEGER)"
        )
        db.executemany(
            "INSERT INTO despesas_current VALUES (101, 2025, 6, ?, ?, ?)",
            [(120, "19000.00", 11), (120, "-29.57", 12), (137, "50000", 13)],
        )
        query = db.execute(rule.sql)
        columns = [column[0] for column in query.description]
        candidates = [
            dict(zip(columns, values, strict=True)) for values in query.fetchall()
        ]
    assert len(candidates) == 1
    result = rule.evaluate(candidates[0])
    assert result is not None
    assert result.amount == Decimal("0.01")
    assert result.evidence["documentos"] == [11, 12]


def test_synthetic_policy_injection_does_not_change_production_coverage() -> None:
    synthetic = R01LocacaoVeiculos()
    synthetic.policies = (
        MonthlyCapPolicy(
            date(2099, 1, 1),
            date(2100, 1, 1),
            Decimal("12713.00"),
            date(2099, 1, 1),
            "cenário sintético",
            ("https://example.invalid/policy",),
        ),
    )
    candidate = row(2099, 1, "12713.01", 120)
    assert synthetic.evaluate(candidate) is not None
    assert HistoricalRentalRule().coverage_reason(candidate)

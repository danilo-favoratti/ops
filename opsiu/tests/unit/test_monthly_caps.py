"""Monthly cap SQL and evaluators against the bounded synthetic source fixture."""

import json
from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.domain.errors import DomainError
from opsiu.domain.models import Despesa
from opsiu.domain.policies import CAPS
from opsiu.domain.rules import registry
from opsiu.domain.rules.base import Rule

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).parents[1] / "fixtures/mini_dataset/Ano-2099.json"
CAP_RULES = tuple(
    rule
    for rule in registry.RULES
    if rule.descriptor.id in {"R01", "R02", "R03", "R04", "R05", "R06"}
)


def create_tables(db: DuckDBPyConnection) -> None:
    db.execute(
        "CREATE TABLE despesas_current (numero_deputado_id BIGINT, ano INTEGER, "
        "mes INTEGER, numero_sub_cota INTEGER, valor_liquido DECIMAL(18,2), "
        "id_documento BIGINT)"
    )
    db.execute(
        'CREATE TABLE caps(category_code INTEGER, "limit" DECIMAL(18,2), '
        "valid_from DATE)"
    )


@pytest.fixture(scope="module")
def fixture_db() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect(":memory:") as db:
        create_tables(db)
        records = [
            Despesa.from_raw(raw) for raw in json.loads(FIXTURE.read_text())["dados"]
        ]
        db.executemany(
            "INSERT INTO despesas_current VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    record.numero_deputado_id,
                    record.ano,
                    record.mes,
                    record.numero_sub_cota,
                    record.valor_liquido,
                    record.id_documento,
                )
                for record in records
            ],
        )
        db.executemany(
            "INSERT INTO caps VALUES (?, ?, ?)",
            [(cap.category_code, cap.limit, cap.valid_from) for cap in CAPS],
        )
        yield db


def candidates(db: DuckDBPyConnection, rule: Rule) -> list[Mapping[str, object]]:
    query = db.execute(rule.sql)
    names = [column[0] for column in query.description]
    return [dict(zip(names, row, strict=True)) for row in query.fetchall()]


@pytest.mark.parametrize("rule_type", CAP_RULES, ids=lambda rule: rule.descriptor.id)
@pytest.mark.parametrize("month,offset", [(1, "0.01"), (2, "-0.01"), (3, "0.00")])
def test_each_cap_aggregates_positive_negative_and_equal_fixture_months(
    fixture_db: DuckDBPyConnection,
    rule_type: type[Rule],
    month: int,
    offset: str,
) -> None:
    rule = rule_type()
    row = next(
        row
        for row in candidates(fixture_db, rule)
        if row["numero_deputado_id"] == 101 and row["mes"] == month
    )
    total, limit = row["total"], row["limite"]
    assert isinstance(total, Decimal) and isinstance(limit, Decimal)
    assert total - limit == Decimal(offset)
    result = rule.evaluate(row)
    if month != 1:
        assert result is None
        return
    assert result is not None
    assert result.amount == Decimal("0.01")
    documents = result.evidence["documentos"]
    assert isinstance(documents, list)
    assert len(documents) == 2 and documents == sorted(documents)
    assert result.id_documento == max(documents)
    assert result.evidence == {
        "total": total,
        "limite": limit,
        "documentos": documents,
        "numero_deputado_id": 101,
        "ano": 2099,
        "mes": 1,
        "numero_sub_cota": row["numero_sub_cota"],
        "vigente_desde": date(2023, 1, 1),
    }


@pytest.mark.parametrize("rule_type", CAP_RULES, ids=lambda rule: rule.descriptor.id)
def test_policy_selection_uses_first_day_and_only_latest_effective_version(
    rule_type: type[Rule],
) -> None:
    rule = rule_type()
    category = CAPS[int(rule.descriptor.id[1:]) - 1].category_code
    with duckdb.connect(":memory:") as db:
        create_tables(db)
        db.executemany(
            "INSERT INTO despesas_current VALUES (1, 2099, ?, ?, 150.00, ?)",
            [(month, category, month) for month in (1, 2, 3)],
        )
        db.executemany(
            "INSERT INTO caps VALUES (?, ?, ?)",
            [
                (category, Decimal("100.00"), date(2098, 1, 1)),
                (category, Decimal("200.00"), date(2099, 2, 1)),
                (category, Decimal("120.00"), date(2099, 2, 15)),
                (category, Decimal("999.00"), date(2100, 1, 1)),
                (999, Decimal("1.00"), date(2098, 1, 1)),
            ],
        )
        rows = candidates(db, rule)
    assert len(rows) == 3
    assert [
        (row["mes"], row["total"], row["limite"], row["vigente_desde"]) for row in rows
    ] == [
        (1, Decimal("150.00"), Decimal("100.00"), date(2098, 1, 1)),
        (2, Decimal("150.00"), Decimal("200.00"), date(2099, 2, 1)),
        (3, Decimal("150.00"), Decimal("120.00"), date(2099, 2, 15)),
    ]
    outcomes = [rule.evaluate(row) for row in rows]
    assert [result.amount if result else None for result in outcomes] == [
        Decimal("50.00"),
        None,
        Decimal("30.00"),
    ]


@pytest.mark.parametrize("rule_type", CAP_RULES, ids=lambda rule: rule.descriptor.id)
def test_future_only_policy_is_an_explicit_failure_not_a_missing_candidate(
    rule_type: type[Rule],
) -> None:
    rule = rule_type()
    category = CAPS[int(rule.descriptor.id[1:]) - 1].category_code
    with duckdb.connect(":memory:") as db:
        create_tables(db)
        db.execute(
            "INSERT INTO despesas_current VALUES (1, 2022, 12, ?, 10, 1)", [category]
        )
        db.execute("INSERT INTO caps VALUES (?, 100, DATE '2023-01-01')", [category])
        rows = candidates(db, rule)
    assert len(rows) == 1
    with pytest.raises(DomainError, match="no category cap is effective"):
        rule.evaluate(rows[0])

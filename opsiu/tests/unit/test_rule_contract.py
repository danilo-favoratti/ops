"""Stable finding identities and exact rule result values."""

from datetime import date
from decimal import Decimal

import pytest

from opsiu.domain.errors import DomainError
from opsiu.domain.findings import evidence_json, finding_key
from opsiu.domain.rules.base import RuleResult

pytestmark = pytest.mark.unit


def test_finding_identity_changes_with_each_key_component() -> None:
    assert finding_key("R01", "1", 10) == finding_key("R01", "1", 10)
    assert (
        len(
            {
                finding_key("R01", "1", 10),
                finding_key("R02", "1", 10),
                finding_key("R01", "2", 10),
                finding_key("R01", "1", 11),
            }
        )
        == 4
    )


def test_evidence_encoding_is_canonical_exact_and_rejects_nonfinite_values() -> None:
    assert evidence_json(
        {"b": date(2099, 1, 5), "a": Decimal("90071992547409.91")}
    ) == ('{"a":"90071992547409.91","b":"2099-01-05"}')
    assert evidence_json({"b": 2, "a": 1}) == evidence_json({"a": 1, "b": 2})
    for invalid in (Decimal("NaN"), float("nan")):
        with pytest.raises(ValueError):
            evidence_json({"valor": invalid})


@pytest.mark.parametrize(
    "amount", [Decimal("1.001"), Decimal("NaN"), Decimal("Infinity")]
)
def test_rule_result_rejects_non_cents_and_nonfinite_amounts(amount: Decimal) -> None:
    with pytest.raises(DomainError):
        RuleResult(1, amount, {})

"""Published policy values, source-table coverage, and immutable dated data."""

import re
from collections import Counter
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from opsiu.domain.money import parse_money
from opsiu.domain.policies import (
    ADICIONAIS,
    CAP_CATEGORY_CODES,
    CAPS,
    COTAS_UF,
    Adicional,
    Cap,
    CotaUF,
)

pytestmark = pytest.mark.unit


def test_state_quotas_match_every_published_uf_and_exact_value() -> None:
    source = Path(__file__).resolve().parents[2] / "cota-parlamentar.md"
    matches = re.findall(
        r"\|\s*([A-Z]{2})\s*\|\s*([0-9.]+,[0-9]{2})(?=\s*\|)",
        source.read_text(encoding="utf-8"),
    )
    expected = {uf: parse_money(value) for uf, value in matches}

    assert len(matches) == len(expected) == 27
    assert {quota.uf: quota.value for quota in COTAS_UF} == expected


def test_monthly_caps_have_unique_verified_category_codes_and_exact_limits() -> None:
    expected = {
        "R01": (120, Decimal("12713.00")),
        "R02": (122, Decimal("2700.00")),
        "R03": (3, Decimal("9392.00")),
        "R04": (8, Decimal("8700.00")),
        "R05": (137, Decimal("7697.17")),
        "R06": (40, Decimal("4148.80")),
    }

    assert len({cap.category_code for cap in CAPS}) == len(CAPS) == 6
    assert dict(CAP_CATEGORY_CODES) == {
        rule: code for rule, (code, _) in expected.items()
    }
    assert {cap.category_code: cap.limit for cap in CAPS} == dict(expected.values())


def test_role_additions_are_unique_and_preserve_the_three_monthly_amounts() -> None:
    assert len({addition.role for addition in ADICIONAIS}) == len(ADICIONAIS) == 14
    assert Counter(addition.value for addition in ADICIONAIS) == {
        Decimal("1353.04"): 10,
        Decimal("902.02"): 3,
        Decimal("5075.62"): 1,
    }
    assert all("mercosul" not in addition.role for addition in ADICIONAIS)


def test_policy_rows_are_frozen_and_have_the_documented_effective_date() -> None:
    rows: tuple[Cap | CotaUF | Adicional, ...] = (*CAPS, *COTAS_UF, *ADICIONAIS)
    for policy in rows:
        assert policy.valid_from == date(2023, 1, 1)
        with pytest.raises(FrozenInstanceError):
            setattr(policy, "valid_from", date(2024, 1, 1))

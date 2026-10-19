"""Exact source money parsing, rounding, and Portuguese formatting."""

from decimal import Decimal, Inexact, localcontext

import pytest

from opsiu.domain.errors import DomainError
from opsiu.domain.money import brl, parse_money

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1467", "1467.00"),
        ("1.234,56", "1234.56"),
        ("-12.5", "-12.50"),
        ("1.234.567,89", "1234567.89"),
        ("1.234", "1.23"),
        ("0.005", "0.01"),
        ("-0.005", "-0.01"),
        ("-0.004", "0.00"),
        (" 12,50 ", "12.50"),
        (0, "0.00"),
        (Decimal("12.345"), "12.35"),
        ("123456789012345678901234567890.125", "123456789012345678901234567890.13"),
    ],
)
def test_money_is_rounded_to_two_decimal_places(raw: object, expected: str) -> None:
    amount = parse_money(raw)

    assert amount == Decimal(expected)
    assert amount.as_tuple().exponent == -2
    assert str(amount) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "unknown",
        "1,234.56",
        "1.23,45",
        "1.234.567",
        "1e3",
        "NaN",
        "Infinity",
        Decimal("NaN"),
        Decimal("Infinity"),
        True,
        False,
        12.5,
        None,
    ],
)
def test_malformed_or_inexact_money_raises_domain_error(raw: object) -> None:
    with pytest.raises(DomainError):
        parse_money(raw)


def test_rounding_does_not_depend_on_or_modify_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = True
        assert parse_money("1234.565") == Decimal("1234.57")
        assert context.prec == 3
        assert context.traps[Inexact]


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("1234.56", "R$ 1.234,56"), ("-12.5", "R$ -12,50"), ("0", "R$ 0,00")],
)
def test_brl_formats_portuguese_currency(amount: str, expected: str) -> None:
    assert brl(Decimal(amount)) == expected

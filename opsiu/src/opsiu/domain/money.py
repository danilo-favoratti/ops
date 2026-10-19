"""Exact monetary parsing and BRL display, without binary floating-point input."""

import re
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation, localcontext

from opsiu.domain.errors import DomainError

CENT = Decimal("0.01")
_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")
_BRAZILIAN = re.compile(r"[+-]?(?:[0-9]+|[0-9]{1,3}(?:\.[0-9]{3})+),[0-9]+")


def parse_money(value: object) -> Decimal:
    """Parse a finite amount and round to cents with explicit ROUND_HALF_UP.

    A comma selects Brazilian decimal/grouping notation. Without a comma,
    the source uses a dot as its decimal separator. Floats and booleans are
    rejected rather than silently changing their meaning.
    """
    if isinstance(value, bool):
        raise DomainError("boolean values are not monetary amounts")
    if isinstance(value, str):
        text = value.strip()
        pattern = _BRAZILIAN if "," in text else _DECIMAL
        if pattern.fullmatch(text) is None:
            raise DomainError(f"invalid monetary amount: {value!r}")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        amount = Decimal(text)
    elif isinstance(value, (int, Decimal)):
        amount = Decimal(value)
    else:
        raise DomainError("monetary amounts require a string, integer, or Decimal")
    if not amount.is_finite():
        raise DomainError("monetary amounts must be finite")
    try:
        with localcontext(Context(prec=max(28, amount.adjusted() + 3))):
            rounded = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise DomainError("monetary amount cannot be represented in cents") from error
    return Decimal("0.00") if rounded.is_zero() else rounded


def brl(value: Decimal) -> str:
    """Format an exact amount as Portuguese BRL text, including negative values."""
    formatted = f"{parse_money(value):,.2f}"
    return "R$ " + formatted.translate(str.maketrans(",.", ".,"))

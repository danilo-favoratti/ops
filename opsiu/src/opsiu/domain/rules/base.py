"""Typed contract for deterministic rules and their candidate evaluations."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from opsiu.domain.errors import DomainError
from opsiu.domain.money import parse_money


@dataclass(frozen=True, slots=True)
class RuleDescriptor:
    id: str
    version: str
    title: str
    severity: str
    fundamento: str
    needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.id,
                self.version,
                self.title,
                self.severity,
                self.fundamento,
            )
        ):
            raise DomainError("rule descriptor fields must not be empty")
        if any(not name.strip() for name in self.needs):
            raise DomainError("required relation names must not be empty")


@dataclass(frozen=True, slots=True)
class RuleResult:
    id_documento: int
    amount: Decimal
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.id_documento) is not int:
            raise DomainError("finding document ID must be an integer")
        if not isinstance(self.amount, Decimal) or self.amount != parse_money(
            self.amount
        ):
            raise DomainError("finding amount must be an exact Decimal number of cents")


class Rule(Protocol):
    descriptor: RuleDescriptor
    sql: str

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None: ...

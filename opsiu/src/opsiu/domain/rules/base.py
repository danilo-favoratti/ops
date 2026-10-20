"""Typed contract for deterministic rules and their candidate evaluations."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

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
    combine_document_rows: bool = False
    identify_missing_documents: bool = False
    suspended_reason: str | None = None

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
        if self.suspended_reason is not None and (
            not isinstance(self.suspended_reason, str)
            or not self.suspended_reason.strip()
        ):
            raise DomainError("a suspended rule requires an explicit reason")


@dataclass(frozen=True, slots=True)
class RuleResult:
    id_documento: int
    amount: Decimal
    evidence: Mapping[str, object]
    severity: str | None = None

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


@runtime_checkable
class CoverageAwareRule(Protocol):
    def coverage_reason(self, row: Mapping[str, object]) -> str | None: ...


class SuspendedRule:
    """Fail closed outside the check engine's explicit suspension lifecycle."""

    sql = "SELECT 1 AS suspended WHERE FALSE"

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        raise DomainError("a suspended rule cannot evaluate candidates")

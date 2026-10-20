"""Bounded graph queries over accounting rows, independent of rule findings."""

import re
from dataclasses import dataclass
from typing import Protocol

from opsiu.domain.graph import GraphPage, GraphSearchKind, GraphSearchResults


def _year(value: int | None) -> None:
    if value is not None and (type(value) is not int or not 1 <= value <= 9999):
        raise ValueError("O ano deve estar entre 1 e 9999.")


def _query(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > 120
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("A busca deve ter até 120 caracteres, sem controles.")
    return value.strip() or None


@dataclass(frozen=True, slots=True)
class GraphFilters:
    deputy_id: int | None = None
    supplier_id: str | None = None
    year: int | None = None
    query: str | None = None
    cursor: str | None = None
    limit: int = 40

    def __post_init__(self) -> None:
        _year(self.year)
        object.__setattr__(self, "query", _query(self.query))
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ValueError("Cada página deve conter entre 1 e 100 registros.")
        if self.deputy_id is not None and (
            type(self.deputy_id) is not int or not -(2**63) <= self.deputy_id < 2**63
        ):
            raise ValueError("O código parlamentar deve ser um inteiro válido.")
        if self.supplier_id is not None and (
            not isinstance(self.supplier_id, str)
            or re.fullmatch(
                r"supplier:(?:[0-9]{11}|[0-9]{14}|unverified:[a-f0-9]{64})",
                self.supplier_id,
            )
            is None
        ):
            raise ValueError("A identidade do fornecedor é inválida.")
        if self.cursor is not None and (
            not isinstance(self.cursor, str) or not 1 <= len(self.cursor) <= 2048
        ):
            raise ValueError("O cursor de paginação é inválido.")


@dataclass(frozen=True, slots=True)
class GraphSearch:
    kind: GraphSearchKind
    q: str = ""
    year: int | None = None
    limit: int = 12

    def __post_init__(self) -> None:
        _year(self.year)
        if self.kind not in ("parliamentarian", "supplier"):
            raise ValueError("Escolha parlamentares ou fornecedores.")
        object.__setattr__(self, "q", _query(self.q) or "")
        if type(self.limit) is not int or not 1 <= self.limit <= 20:
            raise ValueError("A busca deve retornar entre 1 e 20 opções.")


class GraphRepository(Protocol):
    def page(self, filters: GraphFilters) -> GraphPage: ...

    def search(self, filters: GraphSearch) -> GraphSearchResults: ...


class GraphService:
    def __init__(self, repository: GraphRepository) -> None:
        self.repository = repository

    def page(self, filters: GraphFilters) -> GraphPage:
        return self.repository.page(filters)

    def search(self, filters: GraphSearch) -> GraphSearchResults:
        return self.repository.search(filters)

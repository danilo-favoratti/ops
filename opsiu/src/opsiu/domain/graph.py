"""Immutable, bounded projections of the reimbursement relationship graph."""

from dataclasses import dataclass
from typing import Literal

from opsiu.domain.errors import DomainError

GraphKind = Literal["parliamentarian", "supplier", "reimbursement"]
GraphSearchKind = Literal["parliamentarian", "supplier"]
GraphValue = str | int | bool | None


class GraphSnapshotError(DomainError):
    """The client must restart its traversal against the current source snapshot."""


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    kind: GraphKind
    label: str
    subtitle: str
    details: dict[str, GraphValue]


@dataclass(frozen=True, slots=True)
class GraphEdge:
    id: str
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class GraphPagination:
    next_cursor: str | None
    has_more: bool
    returned: int
    limit: int


@dataclass(frozen=True, slots=True)
class GraphSummary:
    reimbursements: int
    parliamentarians: int
    suppliers: int
    total_amount: str


@dataclass(frozen=True, slots=True)
class GraphPage:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    page: GraphPagination
    summary: GraphSummary
    snapshot: str


@dataclass(frozen=True, slots=True)
class GraphSearchItem:
    id: str
    label: str
    subtitle: str
    kind: GraphSearchKind


@dataclass(frozen=True, slots=True)
class GraphSearchResults:
    items: tuple[GraphSearchItem, ...]
    limit: int
    has_more: bool

"""Audited, bounded graph endpoints for the existing local review application."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from typing import Literal, cast

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from opsiu.adapters.duckdb.graph_repository import DuckDBGraphRepository
from opsiu.application.graph import GraphFilters, GraphSearch, GraphService
from opsiu.application.ports import JsonValue
from opsiu.domain.errors import DomainError
from opsiu.interfaces.web.runtime import RequestScope
from opsiu.interfaces.web.security import WebError

_ScopeFactory = Callable[[Request], RequestScope]
_Audited = Callable[
    [RequestScope, str, dict[str, JsonValue], str, Callable[[], Response]], Response
]
_Render = Callable[[Request, RequestScope, str, dict[str, object]], Response]
_Complete = Callable[[Response, RequestScope], Response]


def _date(value: str) -> date | None:
    if not value:
        return None
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Informe a data no formato AAAA-MM-DD.")
    return parsed


def register_graph_routes(
    app: FastAPI,
    *,
    scope_for: _ScopeFactory,
    audited: _Audited,
    render: _Render,
    complete: _Complete,
) -> None:
    def failure(scope: RequestScope, error: Exception) -> Response:
        if isinstance(error, WebError):
            status, message = error.status, str(error)
        elif isinstance(error, DomainError):
            status, message = (
                409,
                "Os dados ou os filtros mudaram. Recomece a exploração do grafo.",
            )
        elif isinstance(error, (ValueError, TypeError)):
            status, message = 400, "Confira os filtros e os limites da consulta."
        else:
            status, message = (
                500,
                "Não foi possível carregar o grafo. Consulte a execução registrada.",
            )
        return complete(
            JSONResponse(
                {
                    "error": message,
                    "run_id": scope.run.run_id if scope.run else None,
                },
                status_code=status,
            ),
            scope,
        )

    def parameters(request: Request, names: set[str]) -> dict[str, str]:
        if set(request.query_params) - names or any(
            len(request.query_params.getlist(name)) != 1
            for name in request.query_params
        ):
            raise ValueError("unknown or repeated graph parameter")
        return {name: request.query_params.get(name, "") for name in names}

    @app.get("/graph")
    def graph_page(request: Request) -> Response:
        scope = scope_for(request)
        try:
            return audited(
                scope,
                "graph.page",
                {},
                "abrir_grafo",
                lambda: render(request, scope, "graph.html", {}),
            )
        except Exception as error:
            return failure(scope, error)

    @app.get("/api/graph")
    def graph_data(request: Request) -> Response:
        scope = scope_for(request)

        def load() -> Response:
            raw = parameters(
                request,
                {
                    "deputy_id",
                    "supplier_id",
                    "year",
                    "date_from",
                    "date_to",
                    "query",
                    "cursor",
                    "limit",
                },
            )
            filters = GraphFilters(
                deputy_id=int(raw["deputy_id"]) if raw["deputy_id"] else None,
                supplier_id=raw["supplier_id"] or None,
                year=int(raw["year"]) if raw["year"] else None,
                date_from=_date(raw["date_from"]),
                date_to=_date(raw["date_to"]),
                query=raw["query"] or None,
                cursor=raw["cursor"] or None,
                limit=int(raw["limit"] or "40"),
            )
            service = GraphService(DuckDBGraphRepository(scope.database()))
            result = service.page(filters)
            assert scope.run is not None
            scope.run.counters["graph_nodes_returned"] = len(result.nodes)
            scope.run.counters["graph_edges_returned"] = len(result.edges)
            scope.run.counters["graph_records_returned"] = result.page.returned
            return complete(JSONResponse(asdict(result)), scope)

        try:
            return audited(
                scope,
                "graph.data",
                dict(request.query_params),
                "carregar_conexoes",
                load,
            )
        except Exception as error:
            return failure(scope, error)

    @app.get("/api/graph/search")
    def graph_search(request: Request) -> Response:
        scope = scope_for(request)

        def load() -> Response:
            raw = parameters(
                request, {"kind", "q", "year", "date_from", "date_to", "limit"}
            )
            filters = GraphSearch(
                kind=cast(Literal["parliamentarian", "supplier"], raw["kind"]),
                q=raw["q"],
                year=int(raw["year"]) if raw["year"] else None,
                date_from=_date(raw["date_from"]),
                date_to=_date(raw["date_to"]),
                limit=int(raw["limit"] or "12"),
            )
            service = GraphService(DuckDBGraphRepository(scope.database()))
            result = service.search(filters)
            assert scope.run is not None
            scope.run.counters["graph_search_results"] = len(result.items)
            return complete(JSONResponse(asdict(result)), scope)

        try:
            return audited(
                scope,
                "graph.search",
                dict(request.query_params),
                "buscar_entidades",
                load,
            )
        except Exception as error:
            return failure(scope, error)

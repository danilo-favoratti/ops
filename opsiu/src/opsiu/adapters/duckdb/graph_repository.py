"""Read-only, keyset-paginated projections of current CEAP accounting rows."""

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import cast
from uuid import UUID

from duckdb import DuckDBPyConnection

from opsiu.application.graph import GraphFilters, GraphSearch
from opsiu.domain.graph import (
    GraphEdge,
    GraphNode,
    GraphPage,
    GraphPagination,
    GraphSearchItem,
    GraphSearchResults,
    GraphSnapshotError,
    GraphSummary,
    GraphValue,
)
from opsiu.domain.identifiers import valid_numeric_cnpj_sql
from opsiu.domain.text import normalize_name, normalized_name_sql


def _valid_cpf_sql(column: str) -> str:
    checks = [
        f"regexp_full_match({column}, '[0-9]{{11}}')",
        f"{column} <> repeat(substr({column}, 1, 1), 11)",
    ]
    for index in (10, 11):
        weighted = " + ".join(
            f"try_cast(substr({column}, {position}, 1) AS INTEGER)"
            f" * {index + 1 - position}"
            for position in range(1, index)
        )
        remainder = f"(({weighted}) % 11)"
        checks.append(
            f"try_cast(substr({column}, {index}, 1) AS INTEGER) = "
            f"CASE WHEN {remainder} < 2 THEN 0 ELSE 11 - {remainder} END"
        )
    return " AND ".join(checks)


_VALID_TAX_ID = (
    f"(({valid_numeric_cnpj_sql('cnpj_cpf_digits')})"
    f" OR ({_valid_cpf_sql('cnpj_cpf_digits')}))"
)
_IDENTIFIED = f"""
    WITH identified AS (
        SELECT d.*,
            CASE WHEN {_VALID_TAX_ID}
            THEN 'supplier:' || cnpj_cpf_digits
            ELSE 'supplier:unverified:' || sha256(
                cast(source_file_id AS VARCHAR) || ':' ||
                cast(source_record_number AS VARCHAR))
            END AS supplier_node_id
        FROM despesas_current d
    )
"""
_SNAPSHOT = """
    SELECT sha256(coalesce(string_agg(
        cast(source_file_id AS VARCHAR) || ':' || sha256,
        '|' ORDER BY source_year), ''))
    FROM (
        SELECT source_file_id, sha256, source_year FROM source_files
        WHERE source_year IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY source_year ORDER BY ingested_at DESC, source_file_id DESC
        ) = 1
    )
"""
_VERIFIED_NOTE = (
    "Identidade agrupada pelo CPF/CNPJ com dígitos verificadores válidos; "
    "a validação matemática não confirma a situação cadastral."
)
_UNVERIFIED_NOTE = (
    "CPF/CNPJ ausente ou inválido. Este fornecedor está isolado por registro "
    "para evitar unir pessoas ou empresas sem identidade confirmada."
)


def _fingerprint(filters: GraphFilters) -> str:
    fields = asdict(filters)
    fields.pop("cursor")
    for name, value in (("date_from", filters.date_from), ("date_to", filters.date_to)):
        fields[name] = value.isoformat() if value is not None else None
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _cursor(filters: GraphFilters, snapshot: str, row: Mapping[str, object]) -> str:
    payload = {
        "v": 1,
        "snapshot": snapshot,
        "filters": _fingerprint(filters),
        "source": str(row["source_file_id"]),
        "row": row["source_record_number"],
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _position(filters: GraphFilters, snapshot: str) -> tuple[str, int] | None:
    if filters.cursor is None:
        return None
    try:
        decoded: object = json.loads(
            base64.b64decode(filters.cursor, altchars=b"-_", validate=True)
        )
        if not isinstance(decoded, dict) or set(decoded) != {
            "v",
            "snapshot",
            "filters",
            "source",
            "row",
        }:
            raise ValueError
        if type(decoded["v"]) is not int or decoded["v"] != 1:
            raise ValueError
        for field in ("snapshot", "filters"):
            if (
                not isinstance(decoded[field], str)
                or re.fullmatch("[a-f0-9]{64}", decoded[field]) is None
            ):
                raise ValueError
        if (
            not isinstance(decoded["source"], str)
            or str(UUID(decoded["source"])) != decoded["source"]
            or type(decoded["row"]) is not int
            or not 1 <= decoded["row"] < 2**63
        ):
            raise ValueError
        if decoded["snapshot"] != snapshot:
            raise GraphSnapshotError(
                "Os dados foram atualizados. Reinicie a exploração do grafo."
            )
        if decoded["filters"] != _fingerprint(filters):
            raise ValueError("O cursor pertence a outros filtros. Reinicie a busca.")
        return decoded["source"], decoded["row"]
    except GraphSnapshotError:
        raise
    except (ValueError, TypeError, KeyError, binascii.Error, RecursionError) as error:
        raise ValueError(
            "Cursor inválido ou incompatível. Reinicie a busca."
        ) from error


def _text_search(
    columns: tuple[str, ...], query: str, *, fiscal_id: str | None = None
) -> tuple[str, list[object]]:
    needle = normalize_name(query)
    if not needle:
        return "FALSE", []
    clauses = [
        f"contains(coalesce({normalized_name_sql(column)}, ''), ?)"
        for column in columns
    ]
    parameters: list[object] = [needle] * len(columns)
    # A formatted CPF/CNPJ is searched as digits, without weakening name searches.
    if fiscal_id is not None and not any(char.isalpha() for char in query):
        digits = re.sub("[^0-9]", "", query)
        if digits:
            clauses.append(f"contains(coalesce({fiscal_id}, ''), ?)")
            parameters.append(digits)
    return "(" + " OR ".join(clauses) + ")", parameters


def _filters(filters: GraphFilters) -> tuple[str, list[object]]:
    conditions: list[str] = []
    parameters: list[object] = []
    for column, value in (
        ("numero_deputado_id", filters.deputy_id),
        ("supplier_node_id", filters.supplier_id),
        ("ano", filters.year),
    ):
        if value is not None:
            conditions.append(f"{column} = ?")
            parameters.append(value)
    for operator, bound in ((">=", filters.date_from), ("<=", filters.date_to)):
        if bound is not None:
            conditions.append(f"data_emissao {operator} ?")
            parameters.append(bound)
    if filters.query is not None:
        clause, values = _text_search(
            ("nome_parlamentar", "fornecedor", "descricao", "numero"),
            filters.query,
            fiscal_id="cnpj_cpf_digits",
        )
        if filters.query.isascii() and filters.query.isdigit():
            clause = (
                f"({clause} OR cast(numero_deputado_id AS VARCHAR) = ?"
                " OR cast(id_documento AS VARCHAR) = ?)"
            )
            values.extend((filters.query, filters.query))
        conditions.append(clause)
        parameters.extend(values)
    return (" AND ".join(conditions) if conditions else "TRUE"), parameters


def _optional(value: object) -> str | None:
    return None if value is None else str(value)


def _identity_note(identity: str) -> str:
    return _UNVERIFIED_NOTE if ":unverified:" in identity else _VERIFIED_NOTE


def _nodes(row: Mapping[str, object]) -> tuple[GraphNode, GraphNode, GraphNode]:
    deputy_id = int(str(row["numero_deputado_id"]))
    supplier_id = str(row["supplier_node_id"])
    source_id = str(row["source_file_id"])
    record_number = int(str(row["source_record_number"]))
    receipt_id = f"reimbursement:{source_id}:{record_number}"
    deputy_name = str(row["nome_parlamentar"])
    supplier_name = str(row["fornecedor"] or "Fornecedor não informado")
    details: dict[str, GraphValue] = {
        "amount": str(row["valor_liquido"]),
        "document_amount": str(row["valor_documento"]),
        "disallowed_amount": _optional(row["valor_glosa"]),
        "document_id": cast(int | None, row["id_documento"]),
        "invoice_number": _optional(row["numero"]),
        "date": _optional(row["data_emissao"]),
        "category": _optional(row["descricao"]),
        "category_code": cast(int, row["numero_sub_cota"]),
        "year": cast(int, row["ano"]),
        "month": cast(int, row["mes"]),
        "deputy_id": deputy_id,
        "deputy_name": deputy_name,
        "supplier_name": supplier_name,
        "supplier_tax_id": _optional(row["cnpj_cpf_digits"]),
        "supplier_identity_note": _identity_note(supplier_id),
        "source_file_id": source_id,
        "source_record_number": record_number,
        "source_file": str(row["source_file"]),
        "source_sha256": str(row["source_sha256"]),
        "record_sha256": str(row["record_sha256"]),
    }
    document = row["id_documento"]
    label = (
        f"Documento {document}"
        if isinstance(document, int) and document > 0
        else "Documento sem código"
    )
    return (
        GraphNode(
            f"deputy:{deputy_id}",
            "parliamentarian",
            deputy_name,
            " · ".join(
                str(value) for value in (row["sigla_uf"], row["sigla_partido"]) if value
            ),
            {
                "deputy_id": deputy_id,
                "uf": str(row["sigla_uf"]),
                "party": _optional(row["sigla_partido"]),
            },
        ),
        GraphNode(
            receipt_id,
            "reimbursement",
            label,
            str(row["descricao"] or "Categoria não informada"),
            details,
        ),
        GraphNode(
            supplier_id,
            "supplier",
            supplier_name,
            str(row["cnpj_cpf_digits"] or "Identidade não informada"),
            {
                "supplier_id": supplier_id,
                "tax_id": _optional(row["cnpj_cpf_digits"]),
                "identity_note": _identity_note(supplier_id),
            },
        ),
    )


class DuckDBGraphRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def _snapshot(self) -> str:
        result = self.connection.execute(_SNAPSHOT).fetchone()
        assert result is not None
        return str(result[0])

    def _rows(
        self, sql: str, parameters: list[object]
    ) -> tuple[dict[str, object], ...]:
        cursor = self.connection.execute(sql, parameters)
        names = [column[0] for column in cursor.description]
        return tuple(dict(zip(names, row, strict=True)) for row in cursor.fetchall())

    def page(self, filters: GraphFilters) -> GraphPage:
        snapshot = self._snapshot()
        position = _position(filters, snapshot)
        where, parameters = _filters(filters)
        summary_row = self.connection.execute(
            _IDENTIFIED
            + """SELECT count(*), count(DISTINCT numero_deputado_id),
                        count(DISTINCT supplier_node_id),
                        coalesce(sum(valor_liquido), 0)
                 FROM identified WHERE """
            + where,
            parameters,
        ).fetchone()
        assert summary_row is not None
        summary = GraphSummary(
            int(summary_row[0]),
            int(summary_row[1]),
            int(summary_row[2]),
            str(summary_row[3]),
        )
        page_where = where
        page_parameters = parameters.copy()
        if position is not None:
            page_where += (
                " AND (cast(source_file_id AS VARCHAR), source_record_number) > (?, ?)"
            )
            page_parameters.extend(position)
        rows = self._rows(
            _IDENTIFIED
            + ", selected AS (SELECT * FROM identified WHERE "
            + page_where
            + """ ORDER BY cast(source_file_id AS VARCHAR), source_record_number
                  LIMIT ?)
                SELECT d.*, s.path AS source_file, s.sha256 AS source_sha256
                FROM selected d JOIN source_files s USING (source_file_id)
                ORDER BY cast(source_file_id AS VARCHAR), source_record_number""",
            page_parameters + [filters.limit + 1],
        )
        has_more = len(rows) > filters.limit
        selected = rows[: filters.limit]
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []
        for row in selected:
            deputy, receipt, supplier = _nodes(row)
            for node in (deputy, receipt, supplier):
                nodes[node.id] = node
            edges.extend(
                (
                    GraphEdge(f"{receipt.id}:deputy", deputy.id, receipt.id),
                    GraphEdge(f"{receipt.id}:supplier", receipt.id, supplier.id),
                )
            )
        if self._snapshot() != snapshot:
            raise GraphSnapshotError(
                "Os dados foram atualizados. Reinicie a exploração do grafo."
            )
        return GraphPage(
            tuple(nodes.values()),
            tuple(edges),
            GraphPagination(
                _cursor(filters, snapshot, selected[-1]) if has_more else None,
                has_more,
                len(selected),
                filters.limit,
            ),
            summary,
            snapshot,
        )

    def search(self, filters: GraphSearch) -> GraphSearchResults:
        conditions = ["TRUE"]
        parameters: list[object] = []
        if filters.year is not None:
            conditions.append("ano = ?")
            parameters.append(filters.year)
        for operator, value in ((">=", filters.date_from), ("<=", filters.date_to)):
            if value is not None:
                conditions.append(f"data_emissao {operator} ?")
                parameters.append(value)
        is_deputy = filters.kind == "parliamentarian"
        name = "nome_parlamentar" if is_deputy else "fornecedor"
        if filters.q:
            clause, values = _text_search(
                (name,),
                filters.q,
                fiscal_id=None if is_deputy else "cnpj_cpf_digits",
            )
            if is_deputy and filters.q.isascii() and filters.q.isdigit():
                clause = (
                    f"({clause} OR contains(cast(numero_deputado_id AS VARCHAR), ?))"
                )
                values.append(filters.q)
            conditions.append(clause)
            parameters.extend(values)
        identity = (
            "'deputy:' || cast(numero_deputado_id AS VARCHAR)"
            if is_deputy
            else "supplier_node_id"
        )
        subtitle = (
            "sigla_uf || ' · ' || coalesce(sigla_partido, '')"
            if is_deputy
            else "coalesce(cnpj_cpf_digits, 'Identidade não informada')"
        )
        rows = self._rows(
            _IDENTIFIED
            + f"""SELECT {identity} AS identity,
                    arg_max(coalesce({name}, 'Fornecedor não informado'),
                            (ano, mes, source_file_id, source_record_number)) AS label,
                    arg_max({subtitle},
                            (ano, mes, source_file_id, source_record_number))
                            AS subtitle
                 FROM identified WHERE """
            + " AND ".join(conditions)
            + " GROUP BY identity ORDER BY count(*) DESC, label, identity LIMIT ?",
            parameters + [filters.limit + 1],
        )
        return GraphSearchResults(
            tuple(
                GraphSearchItem(
                    str(row["identity"]),
                    str(row["label"]),
                    str(row["subtitle"])
                    + (
                        " · identidade não confirmada"
                        if ":unverified:" in str(row["identity"])
                        else ""
                    ),
                    filters.kind,
                )
                for row in rows[: filters.limit]
            ),
            filters.limit,
            len(rows) > filters.limit,
        )

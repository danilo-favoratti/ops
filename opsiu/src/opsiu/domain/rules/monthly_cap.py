"""Shared pure SQL and evaluator for non-cumulative monthly category caps."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.rules.base import RuleResult


def monthly_cap_sql(category_code: int) -> str:
    if type(category_code) is not int:
        raise DomainError("category code must be an integer")
    return f"""
        WITH monthly AS (
            SELECT numero_deputado_id, ano, mes, numero_sub_cota,
                   sum(valor_liquido) AS total,
                   max(id_documento) AS id_documento,
                   list(id_documento ORDER BY id_documento) AS documentos
            FROM despesas_current
            WHERE numero_sub_cota = {category_code}
            GROUP BY numero_deputado_id, ano, mes, numero_sub_cota
        )
        SELECT monthly.*, policy.limite, policy.vigente_desde
        FROM monthly
        LEFT JOIN LATERAL (
            SELECT caps."limit" AS limite, caps.valid_from AS vigente_desde
            FROM caps
            WHERE caps.category_code = monthly.numero_sub_cota
              AND caps.valid_from <= make_date(monthly.ano, monthly.mes, 1)
            ORDER BY caps.valid_from DESC
            LIMIT 1
        ) AS policy ON TRUE
        ORDER BY numero_deputado_id, ano, mes
    """


def _integer(row: Mapping[str, object], key: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise DomainError(f"monthly cap candidate {key} must be an integer")
    return value


class MonthlyCapEvaluator:
    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        total, limit, effective = row["total"], row["limite"], row["vigente_desde"]
        if limit is None or effective is None:
            raise DomainError("no category cap is effective for this competence")
        if not isinstance(total, Decimal) or not isinstance(limit, Decimal):
            raise DomainError("monthly total and cap must be exact Decimal values")
        if not total.is_finite() or not limit.is_finite():
            raise DomainError("monthly total and cap must be finite")
        if not isinstance(effective, date):
            raise DomainError("monthly cap effective date must be a date")
        document = _integer(row, "id_documento")
        deputy = _integer(row, "numero_deputado_id")
        year, month = _integer(row, "ano"), _integer(row, "mes")
        category = _integer(row, "numero_sub_cota")
        documents = row["documentos"]
        if (
            not isinstance(documents, list)
            or not documents
            or any(type(value) is not int for value in documents)
        ):
            raise DomainError("monthly document evidence must contain integer IDs")
        if total <= limit:
            return None
        return RuleResult(
            document,
            total - limit,
            {
                "total": total,
                "limite": limit,
                "documentos": list(documents),
                "numero_deputado_id": deputy,
                "ano": year,
                "mes": month,
                "numero_sub_cota": category,
                "vigente_desde": effective,
            },
        )

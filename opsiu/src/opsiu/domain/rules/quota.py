"""Annual cumulative quota estimates with dated monthly allowances."""

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

from opsiu.domain.errors import DomainError
from opsiu.domain.rules.base import RuleDescriptor, RuleResult

# Explicit party/government/minority names observed in published yearly data,
# plus canonical policy names for Congress and vice leadership. Unknown names
# are not inferred from substrings or assigned an invented allowance.
LEADERSHIP_ROLES = MappingProxyType(
    {
        "LIDERANÇA DA MINORIA": "lider_minoria",
        "LIDERANÇA DO GOVERNO NA CÂMARA": "lider_governo_camara",
        "LIDERANÇA DO GOVERNO NO CONGRESSO": "lider_governo_congresso",
        "VICE-LIDERANÇA DA MINORIA": "vice_lider_minoria",
        "VICE-LIDERANÇA DO GOVERNO NA CÂMARA": "vice_lider_governo_camara",
        "VICE-LIDERANÇA DO GOVERNO NO CONGRESSO": "vice_lider_governo_congresso",
        **{
            f"LIDERANÇA DO {party}": "lider_partido"
            for party in (
                "NOVO",
                "PARTIDO REPUBLICANO DA ORDEM SOCIAL",
                "PDT",
                "PODEMOS",
                "PRD",
                "PROGRESSISTAS",
                "PSD",
                "PSDB",
                "PSOL",
                "PT",
                "REPUBLICANOS",
                "SOLIDARIEDADE",
                "UNIÃO BRASIL",
            )
        },
    }
)


def quota_sql(*, leadership: bool) -> str:
    aliases = " ".join(
        f"WHEN '{name}' THEN '{role}'" for name, role in LEADERSHIP_ROLES.items()
    )
    policy_table = "adicionais" if leadership else "cota_uf"
    policy_key = "role" if leadership else "uf"
    identity_key = "cargo" if leadership else "uf"
    base = "0::DECIMAL(18,2)" if leadership else "policy.value"
    addition = "policy.value" if leadership else "0::DECIMAL(18,2)"
    return f"""
        WITH classified AS (
            SELECT *, cpf IS NULL AND sigla_uf = 'NA' AS lideranca,
                   CASE upper(trim(nome_parlamentar)) {aliases} END AS cargo
            FROM despesas_current
        ), identities AS (
            SELECT numero_deputado_id, ano, max(mes) AS ultimo_mes,
                   max(sigla_uf) AS uf, max(cargo) AS cargo,
                   bool_or(lideranca) AS lideranca,
                   count(DISTINCT sigla_uf) AS uf_count,
                   count(DISTINCT lideranca) AS tipo_count,
                   count(DISTINCT cargo) AS cargo_count,
                   count(*) FILTER (WHERE lideranca AND cargo IS NULL)
                       AS cargos_ausentes
            FROM classified GROUP BY numero_deputado_id, ano
        ), spending AS (
            SELECT numero_deputado_id, ano, mes, sum(valor_liquido) AS gasto_mensal,
                   max(id_documento) AS id_documento,
                   list(id_documento ORDER BY id_documento) AS documentos
            FROM classified GROUP BY numero_deputado_id, ano, mes
        ), calendar AS (
            SELECT identities.*, months.mes::INTEGER AS mes
            FROM identities, LATERAL range(1, ultimo_mes + 1) AS months(mes)
            WHERE lideranca = {str(leadership).upper()} OR tipo_count <> 1
        ), monthly AS (
            SELECT calendar.*,
                   coalesce(spending.gasto_mensal, 0::DECIMAL(18,2)) AS gasto_mensal,
                   spending.id_documento, spending.documentos,
                   {base} AS cota_mensal, {addition} AS adicional_mensal,
                   policy.value AS limite_mensal, policy.valid_from AS vigente_desde
            FROM calendar
            LEFT JOIN spending USING (numero_deputado_id, ano, mes)
            LEFT JOIN LATERAL (
                SELECT value, valid_from FROM {policy_table}
                WHERE {policy_key} = calendar.{identity_key}
                  AND valid_from <= make_date(calendar.ano, calendar.mes, 1)
                ORDER BY valid_from DESC LIMIT 1
            ) AS policy ON TRUE
        )
        SELECT *,
               sum(gasto_mensal) OVER annual AS gasto_acumulado,
               sum(limite_mensal) OVER annual AS limite_acumulado,
               count(*) FILTER (WHERE limite_mensal IS NULL) OVER annual
                   AS politicas_ausentes,
               list(struct_pack(mes := mes, gasto := gasto_mensal,
                   cota := cota_mensal, adicional := adicional_mensal,
                   limite := limite_mensal, vigente_desde := vigente_desde))
                   OVER annual AS serie_mensal
        FROM monthly
        WINDOW annual AS (PARTITION BY numero_deputado_id, ano ORDER BY mes
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        QUALIFY id_documento IS NOT NULL
        ORDER BY numero_deputado_id, ano, mes
    """


def _decimal(row: Mapping[str, object], key: str) -> Decimal:
    value = row[key]
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError(f"quota candidate {key} requires a finite Decimal")
    return value


class QuotaEvaluator:
    descriptor: RuleDescriptor
    sql: str

    def __init__(self, margin_percent: Decimal = Decimal("5")) -> None:
        if (
            not isinstance(margin_percent, Decimal)
            or not margin_percent.is_finite()
            or margin_percent < 0
        ):
            raise DomainError("quota margin must be a nonnegative finite Decimal")
        self.margin_percent = margin_percent

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        if row["uf_count"] != 1 or row["tipo_count"] != 1:
            raise DomainError(
                "ambiguous state or leadership classification within source year"
            )
        if row["lideranca"] and (
            row["cargo_count"] != 1 or row["cargos_ausentes"] != 0
        ):
            raise DomainError("unknown or ambiguous leadership role within source year")
        if row["politicas_ausentes"] != 0 or row["vigente_desde"] is None:
            raise DomainError("no quota policy is effective for this competence")
        spent = _decimal(row, "gasto_acumulado")
        allowance = _decimal(row, "limite_acumulado")
        threshold = allowance * (Decimal("1") + self.margin_percent / Decimal("100"))
        document = row["id_documento"]
        if document is None or spent <= threshold:
            return None
        if type(document) is not int:
            raise DomainError("quota finding document ID must be an integer")
        evidence: dict[str, object] = {
            key: row[key]
            for key in (
                "numero_deputado_id",
                "ano",
                "mes",
                "uf",
                "cargo",
                "lideranca",
                "gasto_mensal",
                "gasto_acumulado",
                "limite_acumulado",
                "cota_mensal",
                "adicional_mensal",
                "documentos",
                "serie_mensal",
            )
        }
        evidence.update(
            {
                "approximate": True,
                "margem_percentual": self.margin_percent,
                "limite_com_margem": threshold,
                "disponivel_no_mes": allowance
                - (spent - _decimal(row, "gasto_mensal")),
                "cargos_individuais_disponiveis": False,
            }
        )
        return RuleResult(document, spent - allowance, evidence)

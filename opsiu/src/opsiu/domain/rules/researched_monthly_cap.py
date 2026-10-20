"""Monthly caps with dated research and explicit gaps in policy coverage."""

from collections.abc import Mapping
from datetime import date

from opsiu.domain.errors import DomainError
from opsiu.domain.researched_caps import MonthlyCapPolicy
from opsiu.domain.rules.base import RuleResult
from opsiu.domain.rules.monthly_cap import MonthlyCapEvaluator


def researched_monthly_cap_sql(category_code: int) -> str:
    if type(category_code) is not int:
        raise DomainError("category code must be an integer")
    return f"""
        SELECT numero_deputado_id, ano, mes, numero_sub_cota,
               sum(valor_liquido) AS total,
               max(id_documento) AS id_documento,
               list(id_documento ORDER BY id_documento) AS documentos
        FROM despesas_current
        WHERE numero_sub_cota = {category_code}
        GROUP BY numero_deputado_id, ano, mes, numero_sub_cota
        ORDER BY numero_deputado_id, ano, mes
    """


class ResearchedMonthlyCapEvaluator(MonthlyCapEvaluator):
    policies: tuple[MonthlyCapPolicy, ...]

    def _policy(self, row: Mapping[str, object]) -> MonthlyCapPolicy | None:
        year, month = row["ano"], row["mes"]
        if type(year) is not int or type(month) is not int:
            raise DomainError("monthly competence must contain integer year and month")
        competence = date(year, month, 1)
        matches = [
            policy
            for policy in self.policies
            if policy.start <= competence < policy.end
        ]
        if len(matches) > 1:
            raise DomainError("researched monthly cap periods overlap")
        return matches[0] if matches else None

    def coverage_reason(self, row: Mapping[str, object]) -> str | None:
        if self._policy(row) is None:
            return (
                "Competência sem limite historicamente pesquisado; "
                "não foi aplicada a tabela atual retroativamente."
            )
        return None

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        policy = self._policy(row)
        if policy is None:
            return None
        result = super().evaluate(
            {**row, "limite": policy.limit, "vigente_desde": policy.legal_effective}
        )
        if result is None:
            return None
        evidence = {
            **result.evidence,
            "politica_inicio_competencia": policy.start,
            "politica_fim_exclusivo": policy.end,
            "politica_fontes": list(policy.sources),
            "politica_formula": policy.formula,
            "politica_arredondamento": "ROUND_HALF_UP para centavos",
            "politica_limitacao": policy.limitation,
            "limite_transicao": policy.transition_limit,
            "politica_alteracao_vigente_desde": policy.change_effective,
        }
        return RuleResult(
            result.id_documento,
            result.amount,
            evidence,
            severity="revisar" if policy.limitation else None,
        )

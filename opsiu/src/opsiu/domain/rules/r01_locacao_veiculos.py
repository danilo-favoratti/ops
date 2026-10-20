"""R01: monthly vehicle rental or charter cap."""

from opsiu.domain.researched_caps import RENTAL_CAP_POLICIES
from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.researched_monthly_cap import (
    ResearchedMonthlyCapEvaluator,
    researched_monthly_cap_sql,
)


class R01LocacaoVeiculos(ResearchedMonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R01",
        version="3",
        title="Limite mensal de locação de veículos",
        severity="alta",
        fundamento=(
            "Locação de veículos: limite mensal inacumulável, com atualização "
            "pelo IPCA determinada pelo Ato da Mesa 196/2025 e previsão adicional "
            "do Ato da Mesa 246/2026; aplicar a política da competência."
        ),
        combine_document_rows=True,
        needs=("despesas_current",),
    )
    policies = RENTAL_CAP_POLICIES
    sql = researched_monthly_cap_sql(120)

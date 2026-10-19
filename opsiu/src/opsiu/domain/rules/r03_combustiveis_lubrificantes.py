"""R03: monthly fuel and lubricants cap."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.monthly_cap import MonthlyCapEvaluator, monthly_cap_sql


class R03CombustiveisLubrificantes(MonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R03",
        version="1",
        title="Limite mensal de combustíveis e lubrificantes",
        severity="alta",
        fundamento=(
            "combustíveis e lubrificantes (limite inacumulável de R$ 9.392,00 mensais);"
        ),
        needs=("despesas_current", "caps"),
    )
    sql = monthly_cap_sql(3)

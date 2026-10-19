"""R04: monthly specialized security services cap."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.monthly_cap import MonthlyCapEvaluator, monthly_cap_sql


class R04Seguranca(MonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R04",
        version="1",
        title="Limite mensal de serviços de segurança",
        severity="alta",
        fundamento=(
            "serviços de segurança de empresas especializadas (limite "
            "inacumulável de R$ 8.700,00 mensais);"
        ),
        needs=("despesas_current", "caps"),
    )
    sql = monthly_cap_sql(8)

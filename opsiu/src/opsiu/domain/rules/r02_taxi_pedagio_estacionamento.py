"""R02: monthly taxi, toll and parking cap."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.monthly_cap import MonthlyCapEvaluator, monthly_cap_sql


class R02TaxiPedagioEstacionamento(MonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R02",
        version="1",
        title="Limite mensal de táxi, pedágio e estacionamento",
        severity="alta",
        fundamento=(
            "serviços de táxi, pedágio e estacionamento (limite inacumulável "
            "de R$ 2.700,00 mensais);"
        ),
        needs=("despesas_current", "caps"),
    )
    sql = monthly_cap_sql(122)

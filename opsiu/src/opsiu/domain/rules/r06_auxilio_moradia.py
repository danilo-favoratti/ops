"""R06: monthly supplementary housing allowance cap."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.monthly_cap import MonthlyCapEvaluator, monthly_cap_sql


class R06AuxilioMoradia(MonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R06",
        version="1",
        title="Limite mensal de complementação de auxílio-moradia",
        severity="alta",
        fundamento=(
            "complementação de auxílio-moradia, de acordo com o Ato da Mesa "
            "104/88 (limite inacumulável de R$ 4.148,80 mensais);"
        ),
        needs=("despesas_current", "caps"),
    )
    sql = monthly_cap_sql(40)

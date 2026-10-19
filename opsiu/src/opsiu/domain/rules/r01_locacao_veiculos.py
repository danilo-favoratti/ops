"""R01: monthly vehicle rental or charter cap."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.monthly_cap import MonthlyCapEvaluator, monthly_cap_sql


class R01LocacaoVeiculos(MonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R01",
        version="2",
        title="Limite mensal de locação de veículos",
        severity="alta",
        fundamento=(
            "locação ou fretamento de veículos automotores (limite "
            "inacumulável de R$ 12.713,00 mensais), permitida contratação de seguro;"
        ),
        combine_document_rows=True,
        needs=("despesas_current", "caps"),
    )
    sql = monthly_cap_sql(120)

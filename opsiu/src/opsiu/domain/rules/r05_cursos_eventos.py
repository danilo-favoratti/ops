"""R05: monthly specialized courses and events cap."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.monthly_cap import MonthlyCapEvaluator, monthly_cap_sql


class R05CursosEventos(MonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R05",
        version="1",
        title="Limite mensal de cursos, congressos e eventos",
        severity="alta",
        fundamento=(
            "participação em cursos, congressos ou eventos, realizados por "
            "instituição especializada (limite mensal inacumulável de 25% "
            "do valor da menor cota – hoje R$ 7.697,17);"
        ),
        needs=("despesas_current", "caps"),
    )
    sql = monthly_cap_sql(137)

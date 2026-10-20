"""R05: monthly specialized courses and events cap."""

from opsiu.domain.researched_caps import COURSE_CAP_POLICIES
from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.researched_monthly_cap import (
    ResearchedMonthlyCapEvaluator,
    researched_monthly_cap_sql,
)


class R05CursosEventos(ResearchedMonthlyCapEvaluator):
    descriptor = RuleDescriptor(
        id="R05",
        version="2",
        title="Limite mensal de cursos, congressos e eventos",
        severity="alta",
        fundamento=(
            "Cursos e eventos especializados: limite mensal inacumulável de "
            "25% da menor cota mensal, conforme o art. 2º, XIII, do Ato da "
            "Mesa 43/2009; aplicar a tabela vigente na competência."
        ),
        combine_document_rows=True,
        needs=("despesas_current",),
    )
    policies = COURSE_CAP_POLICIES
    sql = researched_monthly_cap_sql(137)

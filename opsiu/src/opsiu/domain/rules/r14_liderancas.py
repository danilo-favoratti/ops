"""R14: leadership-account variant of the annual quota estimate."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.quota import QuotaEvaluator, quota_sql


class R14Liderancas(QuotaEvaluator):
    descriptor = RuleDescriptor(
        id="R14",
        version="1",
        title="Estimativa do limite acumulado de lideranças",
        severity="media",
        fundamento=(
            "O adicional não é cumulativo caso o parlamentar exerça "
            "mais de um desses cargos."
        ),
        needs=("despesas_current", "adicionais"),
    )
    sql = quota_sql(leadership=True)

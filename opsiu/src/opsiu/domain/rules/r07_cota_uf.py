"""R07: estimated annual state quota with unused monthly balance."""

from opsiu.domain.rules.base import RuleDescriptor
from opsiu.domain.rules.quota import QuotaEvaluator, quota_sql


class R07CotaUF(QuotaEvaluator):
    descriptor = RuleDescriptor(
        id="R07",
        version="1",
        title="Estimativa do limite acumulado da cota por UF",
        severity="media",
        fundamento=(
            "O saldo mensal não utilizado em um mês acumula-se ao longo do "
            "exercício financeiro, vedada a acumulação de um exercício "
            "financeiro para o seguinte."
        ),
        needs=("despesas_current", "cota_uf"),
    )
    sql = quota_sql(leadership=False)

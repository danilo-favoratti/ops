"""R10: competence-month proxy exceeds ninety days after invoice issuance."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.rules.base import RuleDescriptor, RuleResult


class R10PrazoReembolso:
    descriptor = RuleDescriptor(
        id="R10",
        version="1",
        title="Competência posterior ao prazo de 90 dias",
        severity="media",
        fundamento=(
            "O deputado tem até 90 dias para apresentar a documentação "
            "comprobatória do gasto."
        ),
        needs=("despesas_current",),
    )
    sql = """
        SELECT id_documento, data_emissao, make_date(ano, mes, 1) AS competencia,
               valor_liquido
        FROM despesas_current
        WHERE data_emissao IS NOT NULL
          AND make_date(ano, mes, 1) - data_emissao > 90
        ORDER BY id_documento
    """

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        issued, competence = row["data_emissao"], row["competencia"]
        if issued is None:
            return None
        if not isinstance(issued, date) or not isinstance(competence, date):
            raise DomainError("deadline candidate requires issue and competence dates")
        days = (competence - issued).days
        if days <= 90:
            return None
        document, amount = row["id_documento"], row["valor_liquido"]
        if type(document) is not int or not isinstance(amount, Decimal):
            raise DomainError(
                "deadline candidate requires an integer ID and Decimal amount"
            )
        return RuleResult(
            document,
            amount,
            {
                "data_emissao": issued,
                "competencia": competence,
                "dias_decorridos": days,
                "prazo_dias": 90,
                "competencia_e_proxy": True,
            },
        )

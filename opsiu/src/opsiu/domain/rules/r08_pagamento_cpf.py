"""R08: payments to individuals outside the allowed expense categories."""

from collections.abc import Mapping
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.rules.base import RuleDescriptor, RuleResult

ALLOWED_CPF_CATEGORIES = frozenset({1, 119, 121, 122})


class R08PagamentoCPF:
    descriptor = RuleDescriptor(
        id="R08",
        version="1",
        title="Pagamento a pessoa física fora das exceções",
        severity="alta",
        fundamento=(
            "pagamento realizado à pessoa física, salvo para locação de "
            "imóvel, uso de aeronave ou embarcação, e serviços de táxi;"
        ),
        needs=("despesas_current",),
    )
    sql = """
        SELECT id_documento, payer_kind, cnpj_cpf_digits, numero_sub_cota,
               descricao, valor_liquido
        FROM despesas_current
        WHERE payer_kind = 'CPF' AND numero_sub_cota NOT IN (1, 119, 121, 122)
        ORDER BY id_documento
    """

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        category = row["numero_sub_cota"]
        if type(category) is not int:
            raise DomainError("payee candidate category must be an integer")
        if row["payer_kind"] != "CPF" or category in ALLOWED_CPF_CATEGORIES:
            return None
        document, amount, payee = (
            row["id_documento"],
            row["valor_liquido"],
            row["cnpj_cpf_digits"],
        )
        if type(document) is not int or not isinstance(amount, Decimal):
            raise DomainError(
                "payee candidate requires an integer ID and Decimal amount"
            )
        if not isinstance(payee, str) or not payee:
            raise DomainError("CPF finding requires normalized payee digits")
        return RuleResult(
            document,
            amount,
            {
                "payer_kind": "CPF",
                "cnpj_cpf_digits": payee,
                "numero_sub_cota": category,
                "descricao": row["descricao"],
                "valor_liquido": amount,
            },
        )

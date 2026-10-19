"""R09: potential invoice duplicates with exact supplier/number/gross matches."""

from collections.abc import Mapping
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.rules.base import RuleDescriptor, RuleResult


class R09NotaDuplicada:
    descriptor = RuleDescriptor(
        id="R09",
        version="1",
        title="Possível duplicidade de nota fiscal",
        severity="media",
        fundamento=(
            "O parlamentar assume inteira responsabilidade pela nota fiscal "
            "que apresenta (art. 4º do Ato da Mesa 43/2009)."
        ),
        needs=("despesas_current",),
    )
    sql = """
        WITH duplicate_groups AS (
            SELECT cnpj_cpf_digits, numero, valor_documento,
                   list(DISTINCT id_documento ORDER BY id_documento) AS documentos
            FROM despesas_current
            WHERE cnpj_cpf_digits IS NOT NULL AND trim(cnpj_cpf_digits) <> ''
              AND numero IS NOT NULL AND trim(numero) <> ''
              AND valor_documento IS NOT NULL
            GROUP BY cnpj_cpf_digits, numero, valor_documento
            HAVING count(DISTINCT id_documento) > 1
        )
        SELECT DISTINCT expenses.id_documento, duplicates.cnpj_cpf_digits,
               duplicates.numero, duplicates.valor_documento, duplicates.documentos
        FROM despesas_current AS expenses
        JOIN duplicate_groups AS duplicates
          USING (cnpj_cpf_digits, numero, valor_documento)
        ORDER BY id_documento
    """

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        documents = row["documentos"]
        if not isinstance(documents, list) or any(
            type(value) is not int for value in documents
        ):
            raise DomainError("duplicate group requires integer document IDs")
        group = sorted(set(documents))
        if len(group) < 2:
            return None
        document, amount = row["id_documento"], row["valor_documento"]
        if type(document) is not int or not isinstance(amount, Decimal):
            raise DomainError(
                "duplicate candidate requires an integer ID and Decimal amount"
            )
        if document not in group:
            raise DomainError("duplicate candidate document is absent from its group")
        payee, number = row["cnpj_cpf_digits"], row["numero"]
        if (
            not isinstance(payee, str)
            or not payee.strip()
            or not isinstance(number, str)
            or not number.strip()
        ):
            return None
        return RuleResult(
            document,
            amount,
            {
                "cnpj_cpf_digits": payee,
                "numero": number,
                "valor_documento": amount,
                "documentos": group,
                "possivel_duplicidade": True,
            },
        )

"""R09: potential duplicates with usable numbers and matching known issue dates."""

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from opsiu.domain.errors import DomainError
from opsiu.domain.invoice_numbers import (
    usable_invoice_number,
    usable_invoice_number_sql,
)
from opsiu.domain.rules.base import RuleDescriptor, RuleResult


class R09NotaDuplicada:
    descriptor = RuleDescriptor(
        id="R09",
        version="3",
        title="Possível duplicidade de nota fiscal",
        severity="media",
        fundamento=(
            "O parlamentar assume inteira responsabilidade pela nota fiscal "
            "que apresenta (art. 4º do Ato da Mesa 43/2009)."
        ),
        combine_document_rows=True,
        needs=("despesas_current",),
    )
    sql = f"""
        WITH eligible_expenses AS (
            SELECT * FROM despesas_current
            WHERE id_documento > 0
              AND cnpj_cpf_digits IS NOT NULL AND trim(cnpj_cpf_digits) <> ''
              AND {usable_invoice_number_sql("numero")}
              AND valor_documento IS NOT NULL
              AND data_emissao IS NOT NULL
        ), duplicate_groups AS (
            SELECT cnpj_cpf_digits, numero, valor_documento, data_emissao,
                   list(DISTINCT id_documento ORDER BY id_documento) AS documentos
            FROM eligible_expenses
            GROUP BY cnpj_cpf_digits, numero, valor_documento, data_emissao
            HAVING count(DISTINCT id_documento) > 1
        )
        SELECT DISTINCT expenses.id_documento, duplicates.cnpj_cpf_digits,
               duplicates.numero, duplicates.valor_documento, duplicates.data_emissao,
               duplicates.documentos
        FROM eligible_expenses AS expenses
        JOIN duplicate_groups AS duplicates
          USING (cnpj_cpf_digits, numero, valor_documento, data_emissao)
        ORDER BY id_documento
    """

    def with_cached_receipts(self, matches: Mapping[int, str]) -> "R09NotaDuplicada":
        """Include raw-byte matches prepared by the adapter for this run."""
        values: list[str] = []
        for document, evidence in sorted(matches.items()):
            if type(document) is not int or document <= 0:
                raise DomainError("receipt matches require positive document IDs")
            encoded = evidence.replace("'", "''")
            values.append(f"({document}, '{encoded}')")
        receipts_sql = (
            "SELECT * FROM (VALUES "
            + ",".join(values)
            + ") AS r(id_documento,evidence_json)"
            if values
            else (
                "SELECT NULL::BIGINT id_documento, "
                "NULL::VARCHAR evidence_json WHERE FALSE"
            )
        )
        rule = type(self)()
        rule.sql = f"""
            WITH receipt_matches AS ({receipts_sql}),
            metadata_matches AS ({type(self).sql}),
            receipt_rows AS (
                SELECT d.id_documento, d.cnpj_cpf_digits, d.numero,
                       coalesce(d.valor_documento, 0)::DECIMAL(18,2) AS valor_documento,
                       d.data_emissao, r.evidence_json
                FROM despesas_current d JOIN receipt_matches r USING(id_documento)
                JOIN source_files f USING(source_file_id)
                WHERE NOT EXISTS(SELECT 1 FROM metadata_matches m
                                 WHERE m.id_documento=d.id_documento)
                QUALIFY row_number() OVER (PARTITION BY d.id_documento
                    ORDER BY f.path, d.record_sha256)=1
            )
            SELECT *, NULL::VARCHAR AS receipt_evidence FROM metadata_matches
            UNION ALL
            SELECT id_documento,cnpj_cpf_digits,numero,
                   valor_documento,
                   data_emissao, []::BIGINT[] AS documentos,
                   evidence_json AS receipt_evidence FROM receipt_rows
            ORDER BY id_documento
        """
        return rule

    def evaluate(self, row: Mapping[str, object]) -> RuleResult | None:
        receipt_evidence = row.get("receipt_evidence")
        if receipt_evidence is not None:
            if not isinstance(receipt_evidence, str):
                raise DomainError("receipt evidence must be serialized JSON")
            groups = json.loads(receipt_evidence)
            document, amount = row["id_documento"], row["valor_documento"]
            if (
                type(document) is not int
                or document <= 0
                or not isinstance(amount, Decimal)
            ):
                raise DomainError(
                    "receipt match requires a positive document and exact amount"
                )
            if (
                not isinstance(groups, list)
                or not groups
                or any(
                    not isinstance(group, dict)
                    or group.get("comparacao_bytes") is not True
                    or not isinstance(group.get("documentos"), list)
                    or document not in group["documentos"]
                    or len(set(group["documentos"])) < 2
                    for group in groups
                )
            ):
                raise DomainError("invalid exact receipt match evidence")
            return RuleResult(
                document,
                amount,
                {
                    "criterio": "comprovantes_identicos_byte_a_byte",
                    "comprovantes_identicos": groups,
                    "possivel_duplicidade": True,
                    "limitacoes": (
                        "Bytes idênticos comprovam reutilização do arquivo, "
                        "não pagamento duplicado. Conferir glosas, parcelas, "
                        "estornos e "
                        "restituições. O valor bruto sinalizado não representa "
                        "automaticamente dano ou montante recuperável."
                    ),
                },
            )
        documents = row["documentos"]
        if not isinstance(documents, list) or any(
            type(value) is not int for value in documents
        ):
            raise DomainError("duplicate group requires integer document IDs")
        group = sorted(value for value in set(documents) if value > 0)
        if len(group) < 2:
            return None
        document, amount = row["id_documento"], row["valor_documento"]
        if type(document) is not int or not isinstance(amount, Decimal):
            raise DomainError(
                "duplicate candidate requires an integer ID and Decimal amount"
            )
        if document <= 0:
            return None
        if document not in group:
            raise DomainError("duplicate candidate document is absent from its group")
        payee, number = row["cnpj_cpf_digits"], row["numero"]
        if (
            not isinstance(payee, str)
            or not payee.strip()
            or not usable_invoice_number(number)
        ):
            return None
        issued = row["data_emissao"]
        if issued is None:
            return None
        if not isinstance(issued, date):
            raise DomainError("duplicate candidate requires a known issue date")
        return RuleResult(
            document,
            amount,
            {
                "cnpj_cpf_digits": payee,
                "numero": number,
                "valor_documento": amount,
                "data_emissao": issued,
                "documentos": group,
                "possivel_duplicidade": True,
                "criterio": "fornecedor_numero_valor_bruto_data_emissao",
                "limitacoes": (
                    "Coincidência de metadados não comprova pagamento duplicado. "
                    "Conferir comprovantes, estornos, glosas e rateios; o valor "
                    "bruto sinalizado não representa automaticamente dano ou "
                    "montante recuperável."
                ),
            },
        )

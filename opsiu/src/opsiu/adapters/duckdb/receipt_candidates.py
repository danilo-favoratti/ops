"""Select complete receipt groups independently of current finding versions."""

import hashlib
import json

from duckdb import DuckDBPyConnection

from opsiu.application.receipts import (
    ReceiptCandidate,
    ReceiptGroup,
    ReceiptSelection,
)
from opsiu.domain.findings import SourceOrigin
from opsiu.domain.invoice_numbers import usable_invoice_number_sql


class DuckDBReceiptCandidates:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def select(self, limit: int, *, uncached_only: bool = False) -> ReceiptSelection:
        groups = self.connection.execute(
            f"""
            WITH urls AS (
                SELECT id_documento, count(DISTINCT url_documento) AS url_count,
                       bool_and(url_documento IS NOT NULL
                                AND trim(url_documento) <> '') AS has_urls
                FROM despesas_current GROUP BY id_documento
            )
            SELECT d.cnpj_cpf_digits, d.numero, d.valor_documento,
                   list(DISTINCT d.id_documento ORDER BY d.id_documento),
                   bool_and(u.url_count = 1) AS unambiguous,
                   bool_and(u.has_urls) AS complete,
                   ({usable_invoice_number_sql("d.numero")}
                       AND count(DISTINCT d.data_emissao) = 1
                       AND bool_and(d.data_emissao IS NOT NULL)) AS strong_metadata
            FROM despesas_current d JOIN urls u USING (id_documento)
            WHERE d.id_documento > 0
              AND d.cnpj_cpf_digits IS NOT NULL AND trim(d.cnpj_cpf_digits) <> ''
              AND d.numero IS NOT NULL AND trim(d.numero) <> ''
            GROUP BY d.cnpj_cpf_digits, d.numero, d.valor_documento
            HAVING count(DISTINCT d.id_documento) >= 2
            ORDER BY strong_metadata DESC, d.valor_documento DESC,
                     d.cnpj_cpf_digits, d.numero
            """
        ).fetchall()
        cached_documents = (
            {
                int(row[0])
                for row in self.connection.execute(
                    "SELECT DISTINCT d.id_documento FROM despesas_current d "
                    "JOIN nf_texto p ON p.id_documento = d.id_documento "
                    "AND p.url = d.url_documento"
                ).fetchall()
            }
            if uncached_only
            else set()
        )
        selected: list[ReceiptGroup] = []
        documents: dict[int, ReceiptCandidate] = {}
        ambiguous = missing = over_limit = cached = 0
        for supplier, number, amount, identifiers, unambiguous, complete, _ in groups:
            if not complete:
                missing += 1
                continue
            if not unambiguous:
                ambiguous += 1
                continue
            if uncached_only and set(identifiers).issubset(cached_documents):
                cached += 1
                continue
            new_documents = set(identifiers).difference(documents)
            if len(documents) + len(new_documents) > limit:
                over_limit += 1
                continue
            for document in identifiers:
                if document not in documents:
                    documents[document] = self._document(document)
            identity = json.dumps(
                [supplier, number, str(amount)], separators=(",", ":")
            )
            selected.append(
                ReceiptGroup(
                    hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                    tuple(documents[document] for document in identifiers),
                )
            )
        return ReceiptSelection(tuple(selected), ambiguous, missing, over_limit, cached)

    def _document(self, document: int) -> ReceiptCandidate:
        rows = self.connection.execute(
            "SELECT DISTINCT d.url_documento, CAST(d.source_file_id AS VARCHAR), "
            "s.path, s.sha256, d.record_sha256 "
            "FROM despesas_current d JOIN source_files s USING (source_file_id) "
            "WHERE d.id_documento = ? ORDER BY 2, 5",
            [document],
        ).fetchall()
        url = str(rows[0][0])
        keys = self.connection.execute(
            "SELECT DISTINCT f.finding_key FROM findings f "
            "JOIN despesas d ON d.source_file_id = f.source_file_id "
            "AND d.id_documento = f.id_documento "
            "AND d.record_sha256 = f.record_sha256 "
            "WHERE f.status = 'active' AND f.id_documento = ? "
            "AND d.url_documento = ? ORDER BY f.finding_key",
            [document, url],
        ).fetchall()
        return ReceiptCandidate(
            document,
            url,
            tuple(str(key[0]) for key in keys),
            tuple(SourceOrigin(*row[1:]) for row in rows),
        )

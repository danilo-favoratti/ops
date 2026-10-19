"""Prepare verified cached-receipt matches for deterministic duplicate checks."""

import json
from pathlib import Path
from typing import cast

from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.pdf_repository import DuckDBPdfRepository
from opsiu.adapters.files.receipt_comparison import FileReceiptFiles
from opsiu.application.ports import JsonValue
from opsiu.application.receipt_comparison import (
    IdenticalReceipts,
    ReceiptReference,
    compare_receipts,
)
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError


def _group_evidence(group: IdenticalReceipts) -> dict[str, JsonValue]:
    receipts: list[JsonValue] = [
        {
            "id_documento": receipt.id_documento,
            "url": receipt.url,
            "path": str(receipt.path),
            "size_bytes": receipt.size_bytes,
            "sha256": receipt.sha256,
            "snapshot_id": receipt.snapshot_id,
            "snapshot_sha256": receipt.snapshot_sha256,
        }
        for receipt in group.receipts
    ]
    return {
        "documentos": list(group.document_ids),
        "tamanho_bytes": group.size_bytes,
        "sha256": group.sha256,
        "comparacao_bytes": True,
        "recibos": receipts,
    }


def _still_verified(
    original: dict[str, JsonValue], current: dict[str, JsonValue], document: int
) -> bool:
    old_receipts, new_receipts = original.get("recibos"), current.get("recibos")
    documents = original.get("documentos")
    if (
        original.get("comparacao_bytes") is not True
        or original.get("tamanho_bytes") != current.get("tamanho_bytes")
        or original.get("sha256") != current.get("sha256")
        or not isinstance(old_receipts, list)
        or not old_receipts
        or not isinstance(new_receipts, list)
        or not isinstance(documents, list)
        or any(type(value) is not int for value in documents)
        or document not in documents
        or len(set(cast(list[int], documents))) < 2
    ):
        return False
    receipt_documents = {
        receipt.get("id_documento")
        for receipt in old_receipts
        if isinstance(receipt, dict) and type(receipt.get("id_documento")) is int
    }
    return receipt_documents == set(cast(list[int], documents)) and all(
        isinstance(receipt, dict) and receipt in new_receipts
        for receipt in old_receipts
    )


def _original_receipt_groups(original: object) -> list[dict[str, JsonValue]] | None:
    """Find one proof, including the accounting-row wrapper applied by check."""
    if not isinstance(original, dict):
        raise DomainError("Evidência anterior do achado não é um objeto")
    proofs: list[object] = []
    if "comprovantes_identicos" in original:
        proofs.append(original["comprovantes_identicos"])
    if "ocorrencias" in original:
        occurrences = original["ocorrencias"]
        if not isinstance(occurrences, list):
            raise DomainError("Ocorrências inválidas na evidência anterior")
        for occurrence in occurrences:
            if not isinstance(occurrence, dict) or not isinstance(
                occurrence.get("evidencia"), dict
            ):
                raise DomainError("Ocorrência sem evidência válida")
            evidence = occurrence["evidencia"]
            if "comprovantes_identicos" in evidence:
                proofs.append(evidence["comprovantes_identicos"])
    if not proofs:
        return None
    if len(proofs) != 1:
        raise DomainError("Múltiplas provas de comprovantes na evidência anterior")
    groups = proofs[0]
    if (
        not isinstance(groups, list)
        or not groups
        or any(not isinstance(group, dict) for group in groups)
    ):
        raise DomainError("Prova de comprovantes inválida na evidência anterior")
    return cast(list[dict[str, JsonValue]], groups)


def _require_unambiguous_receipt_origins(
    connection: DuckDBPyConnection, run: Run
) -> None:
    """Never anchor cached proof to an arbitrary accounting row or URL."""
    ambiguous = connection.execute(
        "SELECT d.id_documento, count(DISTINCT d.url_documento), "
        "count(*) FILTER (WHERE d.url_documento IS NULL "
        "OR trim(d.url_documento)='') "
        "FROM despesas_current d JOIN "
        "(SELECT DISTINCT id_documento FROM nf_texto WHERE id_documento>0) p "
        "USING (id_documento) GROUP BY d.id_documento "
        "HAVING count(DISTINCT d.url_documento)<>1 "
        "OR count(*) FILTER (WHERE d.url_documento IS NULL "
        "OR trim(d.url_documento)='')>0 ORDER BY d.id_documento"
    ).fetchall()
    for document, url_count, invalid_count in ambiguous:
        run.emit(
            "receipts.ambiguous_document",
            {
                "id_documento": document,
                "urls_distintas": url_count,
                "registros_sem_url_valida": invalid_count,
            },
        )
    if ambiguous:
        raise DomainError(
            "Documento com comprovante em cache tem URLs correntes ambíguas "
            "ou ausentes; checagem não publicada"
        )


def _preserve_active_evidence(
    connection: DuckDBPyConnection, matches: dict[int, list[dict[str, JsonValue]]]
) -> int:
    """Keep exact original proof while its references still verify together.

    A newly cached third copy must not change immutable findings for an earlier
    pair. Full current groups remain in the run events and the new document's
    evidence. Replaced or missing origins cannot justify retaining the old proof.
    """
    retained = 0
    rows = connection.execute(
        "SELECT id_documento, evidence_json FROM findings "
        "WHERE rule_id='R09' AND rule_version='3' AND status='active' "
        "ORDER BY id_documento"
    ).fetchall()
    for document, serialized in rows:
        if document not in matches:
            continue
        old_groups = _original_receipt_groups(json.loads(serialized))
        if old_groups is None:
            continue
        if all(
            any(
                _still_verified(old, current, document) for current in matches[document]
            )
            for old in old_groups
        ):
            matches[document] = old_groups
            retained += 1
    return retained


def prepare_receipt_matches(
    connection: DuckDBPyConnection, external_dir: Path, run: Run
) -> dict[int, str]:
    run.require_step()
    _require_unambiguous_receipt_origins(connection, run)
    references: list[ReceiptReference] = []
    repository = DuckDBPdfRepository(connection)
    rows = connection.execute(
        "SELECT DISTINCT p.id_documento,p.url FROM nf_texto p "
        "JOIN despesas_current d ON d.id_documento=p.id_documento "
        "AND d.url_documento=p.url WHERE p.id_documento>0 ORDER BY 1,2"
    ).fetchall()
    for document, url in rows:
        extraction = repository.latest(document, url)
        assert extraction is not None
        references.append(ReceiptReference.from_pdf(extraction.content))
    result = compare_receipts(
        references, FileReceiptFiles(external_dir), max_receipts=2_000
    )
    run.counters["receipts_compared"] = result.receipts_checked
    run.counters["identical_receipt_groups"] = len(result.groups)
    run.counters["receipt_comparison_failures"] = len(result.failures)
    run.emit(
        "receipts.compared",
        {
            "receipts": result.receipts_checked,
            "identical_groups": len(result.groups),
            "bytes_verified": result.bytes_verified,
            "byte_comparisons": result.byte_comparisons,
            "failures": [failure.error for failure in result.failures],
            "scope": (
                "Somente capturas locais ligadas às URLs correntes; "
                "não cobre PDFs ausentes."
            ),
        },
    )
    if result.failures:
        raise DomainError(
            "Falha de integridade em comprovantes; checagem não publicada"
        )
    matches: dict[int, list[dict[str, JsonValue]]] = {}
    for group in result.groups:
        evidence = _group_evidence(group)
        run.emit("receipts.identical_group", evidence)
        for document in group.document_ids:
            matches.setdefault(document, []).append(evidence)
    run.counters["receipt_evidence_preserved"] = _preserve_active_evidence(
        connection, matches
    )
    return {
        document: json.dumps(evidence_groups, sort_keys=True)
        for document, evidence_groups in sorted(matches.items())
    }

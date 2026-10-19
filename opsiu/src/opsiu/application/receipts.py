"""Bounded acquisition of complete potential-duplicate receipt groups."""

from dataclasses import dataclass
from typing import Protocol

from opsiu.application.ports import JsonValue, PdfRepository, PdfSource
from opsiu.application.runs import ConfigurationError, Run
from opsiu.domain.findings import SourceOrigin


@dataclass(frozen=True)
class ReceiptCandidate:
    id_documento: int
    url: str
    finding_keys: tuple[str, ...]
    sources: tuple[SourceOrigin, ...]


@dataclass(frozen=True)
class ReceiptGroup:
    group_key: str
    documents: tuple[ReceiptCandidate, ...]


@dataclass(frozen=True)
class ReceiptSelection:
    groups: tuple[ReceiptGroup, ...]
    ambiguous_groups: int
    missing_url_groups: int
    over_limit_groups: int
    cached_groups: int


class ReceiptCandidateReader(Protocol):
    def select(
        self, limit: int, *, uncached_only: bool = False
    ) -> ReceiptSelection: ...


def fetch_receipts(
    run: Run,
    candidates: ReceiptCandidateReader,
    source: PdfSource,
    repository: PdfRepository,
    *,
    limit: int,
    uncached_only: bool = False,
) -> None:
    """Capture immutable PDFs without partially selecting an invoice group."""
    if not 2 <= limit <= 200:
        raise ConfigurationError("O limite deve estar entre 2 e 200 documentos.")
    run.counters.update(
        receipt_groups_selected=0,
        receipts_selected=0,
        receipts_extracted=0,
        receipts_cached=0,
        receipts_failed=0,
    )
    with run.step("selecionar_grupos_comprovantes") as selection_step:
        selection = candidates.select(limit, uncached_only=uncached_only)
        documents = {
            item.id_documento: item
            for group in selection.groups
            for item in group.documents
        }
        run.counters.update(
            receipt_groups_selected=len(selection.groups),
            receipt_groups_ambiguous=selection.ambiguous_groups,
            receipt_groups_missing_url=selection.missing_url_groups,
            receipt_groups_over_limit=selection.over_limit_groups,
            receipt_groups_cached=selection.cached_groups,
            receipts_selected=len(documents),
        )
        run.emit(
            "receipts.selected",
            {
                "limit": limit,
                "uncached_only": uncached_only,
                "groups": [
                    {
                        "group_key": group.group_key,
                        "documents": [item.id_documento for item in group.documents],
                    }
                    for group in selection.groups
                ],
                "ambiguous_groups": selection.ambiguous_groups,
                "missing_url_groups": selection.missing_url_groups,
                "over_limit_groups": selection.over_limit_groups,
                "cached_groups": selection.cached_groups,
            },
        )
    if selection_step.record.status == "failed":
        return
    for item in documents.values():
        with run.step(f"obter_comprovante_{item.id_documento}") as step:
            previous = repository.latest(item.id_documento, item.url)
            content = source.prepare(
                item.id_documento,
                item.url,
                previous=previous,
                initial_path_reserved=repository.has_document(item.id_documento),
            )
            extraction = repository.publish(
                content,
                item.finding_keys,
                extraction_id=run.ids.new_id(),
                created_at=run.clock.now(),
                run_id=run.run_id,
                step_id=run.require_step(),
            )
            cached = (
                previous is not None
                and previous.extraction_id == extraction.extraction_id
            )
            run.counters["receipts_cached" if cached else "receipts_extracted"] += 1
            details: dict[str, JsonValue] = {
                "id_documento": item.id_documento,
                "url": item.url,
                "finding_keys": list(item.finding_keys),
                "extraction_id": extraction.extraction_id,
                "snapshot_id": content.snapshot_id,
                "sha256": content.sha256,
                "size_bytes": content.size_bytes,
                "needs_ocr": content.needs_ocr,
                "sources": [
                    {
                        "source_file_id": origin.source_file_id,
                        "source_file": origin.source_file,
                        "source_sha256": origin.source_sha256,
                        "record_sha256": origin.record_sha256,
                    }
                    for origin in item.sources
                ],
            }
            run.emit("receipt.cached" if cached else "receipt.extracted", details)
        if step.record.status == "failed":
            run.counters["receipts_failed"] += 1

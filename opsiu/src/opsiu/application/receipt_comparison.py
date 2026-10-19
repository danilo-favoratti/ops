"""Compare bounded immutable receipt captures without inferring reimbursement fraud."""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self

from opsiu.application.ports import PdfContent
from opsiu.domain.errors import DomainError


@dataclass(frozen=True)
class ReceiptReference:
    id_documento: int
    url: str
    path: Path
    size_bytes: int
    sha256: str
    snapshot_id: str
    snapshot_sha256: str

    @classmethod
    def from_pdf(cls, content: PdfContent) -> Self:
        return cls(
            id_documento=content.id_documento,
            url=content.url,
            path=content.path,
            size_bytes=content.size_bytes,
            sha256=content.sha256,
            snapshot_id=content.snapshot_id,
            snapshot_sha256=content.snapshot_sha256,
        )

    def validate(self) -> None:
        if type(self.id_documento) is not int or self.id_documento <= 0:
            raise DomainError(
                "Comparação de comprovantes exige documento com ID positivo"
            )
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise DomainError("Tamanho do comprovante deve ser positivo")
        if not self.url or not self.snapshot_id:
            raise DomainError("Comprovante sem URL ou identificação da captura")
        if not all(
            re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (self.sha256, self.snapshot_sha256)
        ):
            raise DomainError("Hash SHA-256 inválido na origem do comprovante")


@dataclass(frozen=True)
class ReceiptFingerprint:
    size_bytes: int
    sha256: str


class ReceiptFiles(Protocol):
    def verify(self, receipt: ReceiptReference) -> ReceiptFingerprint:
        """Read the raw file and verify expected size, hash and allowed location."""
        ...

    def identical(self, left: ReceiptReference, right: ReceiptReference) -> bool:
        """Compare raw chunks and revalidate both captures during the same read."""
        ...


@dataclass(frozen=True)
class ReceiptComparisonFailure:
    receipt: ReceiptReference
    error: str


@dataclass(frozen=True)
class IdenticalReceipts:
    size_bytes: int
    sha256: str
    receipts: tuple[ReceiptReference, ...]

    @property
    def document_ids(self) -> tuple[int, ...]:
        return tuple(sorted({receipt.id_documento for receipt in self.receipts}))


@dataclass(frozen=True)
class ReceiptComparison:
    groups: tuple[IdenticalReceipts, ...]
    failures: tuple[ReceiptComparisonFailure, ...]
    receipts_checked: int
    bytes_verified: int
    byte_comparisons: int


def _reference_key(
    receipt: ReceiptReference,
) -> tuple[int, str, str, str, int, str, str]:
    return (
        receipt.id_documento,
        receipt.snapshot_id,
        receipt.url,
        str(receipt.path),
        receipt.size_bytes,
        receipt.sha256,
        receipt.snapshot_sha256,
    )


def compare_receipts(
    receipts: Sequence[ReceiptReference],
    files: ReceiptFiles,
    *,
    max_receipts: int = 200,
    max_total_bytes: int = 256 * 1024 * 1024,
) -> ReceiptComparison:
    """Return exact matches between distinct IDs within a caller-selected sample.

    The limits cover input references and their declared raw bytes, including
    repeated references. File adapters must check the declared size before
    streaming, so a corrupt oversized cache entry cannot bypass that budget.
    Every accepted reference is hash-verified. Only equal verified lengths and
    hashes proceed to raw-byte comparisons; neither metadata nor text suffices.
    A file changing after initial verification aborts rather than returning a
    potentially stale match. Results do not determine whether payments overlap.
    """
    if max_receipts <= 0 or max_total_bytes <= 0:
        raise DomainError("Os limites da comparação devem ser positivos")
    if len(receipts) > max_receipts:
        raise DomainError("A amostra excede o limite de comprovantes da comparação")
    if sum(max(receipt.size_bytes, 0) for receipt in receipts) > max_total_bytes:
        raise DomainError("A amostra excede o limite de bytes da comparação")

    failures: list[ReceiptComparisonFailure] = []
    candidates: dict[tuple[int, str], list[ReceiptReference]] = defaultdict(list)
    bytes_verified = 0
    checked = 0
    for receipt in sorted(set(receipts), key=_reference_key):
        try:
            receipt.validate()
            fingerprint = files.verify(receipt)
            if (fingerprint.size_bytes, fingerprint.sha256) != (
                receipt.size_bytes,
                receipt.sha256,
            ):
                raise DomainError(
                    "Conteúdo do comprovante diverge da origem registrada"
                )
        except DomainError as error:
            failures.append(ReceiptComparisonFailure(receipt, str(error)))
            continue
        candidates[(fingerprint.size_bytes, fingerprint.sha256)].append(receipt)
        bytes_verified += fingerprint.size_bytes
        checked += 1

    groups: list[IdenticalReceipts] = []
    comparisons = 0
    for (size_bytes, sha256), possible in sorted(candidates.items()):
        if len({receipt.id_documento for receipt in possible}) < 2:
            continue
        partitions: list[list[ReceiptReference]] = []
        for receipt in possible:
            for partition in partitions:
                comparisons += 1
                if files.identical(partition[0], receipt):
                    partition.append(receipt)
                    break
            else:
                partitions.append([receipt])
        for partition in partitions:
            if len({receipt.id_documento for receipt in partition}) >= 2:
                groups.append(IdenticalReceipts(size_bytes, sha256, tuple(partition)))
    groups.sort(key=lambda group: (group.document_ids, group.sha256))
    return ReceiptComparison(
        groups=tuple(groups),
        failures=tuple(failures),
        receipts_checked=checked,
        bytes_verified=bytes_verified,
        byte_comparisons=comparisons,
    )

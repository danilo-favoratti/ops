"""Read only the exact immutable cached invoice selected by its provenance."""

import hashlib
from pathlib import Path
from uuid import UUID

from opsiu.application.ports import PdfContent
from opsiu.domain.errors import DomainError


def read_review_pdf(content: PdfContent, external_dir: Path) -> bytes:
    directory = external_dir.resolve() / "pdf"
    path = content.path
    permitted = {
        directory / f"{content.id_documento}.pdf",
        directory
        / "versions"
        / str(content.id_documento)
        / f"{UUID(content.snapshot_id)}.pdf",
    }
    if path not in permitted or path.resolve() != path:
        raise DomainError("PDF cache path does not match its immutable origin")
    data = path.read_bytes()
    if (
        len(data) != content.size_bytes
        or hashlib.sha256(data).hexdigest() != content.sha256
    ):
        raise DomainError("cached PDF hash or size mismatch")
    return data

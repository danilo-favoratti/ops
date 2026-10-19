"""Verified immutable PDF caches and versioned pypdf text extraction."""

import gzip
import hashlib
import re
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID

import httpx
import pypdf

from opsiu.application.ports import PdfContent, PdfExtraction, Snapshot, SnapshotStore
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

_CHUNK_SIZE = 64 * 1024


class _EncryptedPdfError(DomainError):
    """A PDF was captured but its text cannot be extracted without credentials."""


class PdfDocumentSource:
    def __init__(
        self,
        external_dir: Path,
        run: Run,
        snapshot_store: SnapshotStore,
        *,
        allow_unextracted_receipts: bool = False,
    ) -> None:
        self.directory = external_dir.resolve() / "pdf"
        self.run = run
        self.snapshots = snapshot_store
        self.extractor_version = pypdf.__version__
        self.allow_unextracted_receipts = allow_unextracted_receipts

    def prepare(
        self,
        id_documento: int,
        url: str,
        *,
        previous: PdfExtraction | None = None,
        refresh: bool = False,
        initial_path_reserved: bool = False,
    ) -> PdfContent:
        self.run.require_step()
        if type(id_documento) is not int:
            raise DomainError("PDF document ID must be an integer")
        if previous is not None and not refresh:
            content = previous.content
            if content.id_documento != id_documento or content.url != url:
                raise DomainError(
                    "cached PDF does not match the requested document and URL"
                )
            snapshot = self.snapshots.load("pdf", content.snapshot_id)
            self._verify_snapshot(snapshot, url, content.snapshot_sha256)
            self._verify_file(content)
            if (
                content.extractor == "pypdf"
                and content.extractor_version == self.extractor_version
            ):
                return content
            if (
                self.allow_unextracted_receipts
                and content.extractor == "raw-pdf"
                and content.extractor_version == "1"
            ):
                self._verify_pdf_header(content.path)
                return content
            extractor, version, text, pages = self._capture_text(
                content.path, id_documento
            )
            return replace(
                content,
                extractor=extractor,
                extractor_version=version,
                text=text,
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                needs_ocr=not bool(text),
                page_count=pages,
            )
        snapshot = self.snapshots.download("pdf", url)
        self._verify_snapshot(snapshot, url, snapshot.manifest.sha256)
        initial = self.directory / f"{id_documento}.pdf"
        path = (
            self.directory
            / "versions"
            / str(id_documento)
            / f"{snapshot.snapshot_id}.pdf"
            if initial_path_reserved
            or previous is not None
            or initial.exists()
            or initial.is_symlink()
            else initial
        )
        self._safe_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with ExitStack() as stack:
                raw = stack.enter_context(snapshot.path.open("rb"))
                stream: BinaryIO = raw
                encoding = (
                    snapshot.manifest.headers.get("content-encoding", "identity")
                    .strip()
                    .lower()
                )
                if encoding in {"gzip", "x-gzip"}:
                    stream = cast(
                        BinaryIO, stack.enter_context(gzip.GzipFile(fileobj=raw))
                    )
                elif encoding not in {"", "identity"}:
                    raise DomainError(f"unsupported PDF content encoding: {encoding}")
                output = stack.enter_context(path.open("xb"))
                created = True
                digest = hashlib.sha256()
                size = 0
                while chunk := stream.read(_CHUNK_SIZE):
                    self.run.require_step()
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            extractor, version, text, pages = self._capture_text(path, id_documento)
            return PdfContent(
                id_documento,
                url,
                snapshot.snapshot_id,
                snapshot.manifest.sha256,
                path,
                digest.hexdigest(),
                size,
                extractor,
                version,
                text,
                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                not bool(text),
                pages,
            )
        except BaseException:
            if created:
                path.unlink()
            raise

    def _verify_snapshot(self, snapshot: Snapshot, url: str, sha256: str) -> None:
        if (
            snapshot.source != "pdf"
            or snapshot.manifest.sha256 != sha256
            or snapshot.manifest.url != str(httpx.URL(url))
        ):
            raise DomainError("PDF raw snapshot provenance mismatch")

    def _safe_path(self, path: Path) -> None:
        if path.resolve() != path or not path.is_relative_to(self.directory):
            raise DomainError(
                "PDF cache path must remain within its immutable directory"
            )

    def _verify_file(self, content: PdfContent) -> None:
        self._safe_path(content.path)
        expected = {
            self.directory / f"{content.id_documento}.pdf",
            self.directory
            / "versions"
            / str(content.id_documento)
            / f"{UUID(content.snapshot_id)}.pdf",
        }
        if content.path not in expected:
            raise DomainError("PDF cache path does not match its document and snapshot")
        digest = hashlib.sha256()
        size = 0
        with content.path.open("rb") as cached:
            while chunk := cached.read(_CHUNK_SIZE):
                digest.update(chunk)
                size += len(chunk)
        if size != content.size_bytes or digest.hexdigest() != content.sha256:
            raise DomainError("cached PDF hash or size mismatch")
        if (
            hashlib.sha256(content.text.encode("utf-8")).hexdigest()
            != content.text_sha256
        ):
            raise DomainError("cached PDF text hash mismatch")

    def _verify_pdf_header(self, path: Path) -> None:
        with path.open("rb") as source:
            header = source.read(8)
        if re.fullmatch(rb"%PDF-[12]\.[0-9]", header) is None:
            raise DomainError("O comprovante não possui cabeçalho PDF válido.")

    def _capture_text(self, path: Path, document: int) -> tuple[str, str, str, int]:
        if self.allow_unextracted_receipts:
            self._verify_pdf_header(path)
        try:
            text, pages = self._extract(path)
        except (
            _EncryptedPdfError,
            pypdf.errors.PdfReadError,
            pypdf.errors.ParseError,
        ) as error:
            if not self.allow_unextracted_receipts:
                raise
            self.run.emit(
                "receipt.text_unavailable",
                {
                    "id_documento": document,
                    "reason": (
                        "PDF criptografado; bytes preservados sem extração de texto."
                        if isinstance(error, _EncryptedPdfError)
                        else (
                            "PDF com erro de leitura; bytes preservados "
                            "sem extração de texto."
                        )
                    ),
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            )
            return "raw-pdf", "1", "", 0
        return "pypdf", self.extractor_version, text, pages

    def _extract(self, path: Path) -> tuple[str, int]:
        reader = pypdf.PdfReader(path, strict=True)
        try:
            if reader.is_encrypted:
                raise _EncryptedPdfError(
                    "encrypted PDFs cannot be extracted without a password"
                )
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            return text, len(reader.pages)
        finally:
            reader.close()

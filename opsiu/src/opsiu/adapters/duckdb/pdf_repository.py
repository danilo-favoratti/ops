"""Immutable invoice text versions and append-only links to audited findings."""

import hashlib
from dataclasses import asdict
from datetime import datetime

import httpx
from duckdb import DuckDBPyConnection
from pydantic import TypeAdapter

from opsiu.application.ports import PdfContent, PdfExtraction
from opsiu.domain.errors import DomainError

_EXTRACTION = TypeAdapter(PdfExtraction)


class DuckDBPdfRepository:
    def __init__(self, connection: DuckDBPyConnection) -> None:
        self.connection = connection

    def has_document(self, id_documento: int) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM nf_texto WHERE id_documento = ? LIMIT 1", [id_documento]
            ).fetchone()
            is not None
        )

    def latest(self, id_documento: int, url: str) -> PdfExtraction | None:
        return self._read(
            "SELECT * FROM nf_texto WHERE id_documento = ? AND url = ? "
            "ORDER BY created_at DESC, extraction_id DESC LIMIT 1",
            [id_documento, url],
        )

    def _read(self, sql: str, parameters: list[object]) -> PdfExtraction | None:
        cursor = self.connection.execute(sql, parameters)
        row = cursor.fetchone()
        if row is None:
            return None
        values = dict(
            zip((column[0] for column in cursor.description), row, strict=True)
        )
        content = {
            name: values["texto" if name == "text" else name]
            for name in PdfContent.__dataclass_fields__
        }
        content["snapshot_id"] = str(content["snapshot_id"])
        record = _EXTRACTION.validate_python(
            {
                "extraction_id": str(values["extraction_id"]),
                "content": content,
                "created_at": values["created_at"],
                "run_id": str(values["run_id"]),
                "step_id": str(values["step_id"]),
            }
        )
        self._validate_content(record.content)
        return record

    def _validate_content(self, content: PdfContent) -> None:
        if (
            hashlib.sha256(content.text.encode("utf-8")).hexdigest()
            != content.text_sha256
        ):
            raise DomainError("PDF extraction text hash mismatch")
        if (
            content.needs_ocr != (not bool(content.text.strip()))
            or content.size_bytes < 0
            or content.page_count < 0
            or not content.extractor
            or not content.extractor_version
        ):
            raise DomainError("PDF extraction metadata is inconsistent")

    def publish(
        self,
        content: PdfContent,
        finding_keys: tuple[str, ...],
        *,
        extraction_id: str,
        created_at: datetime,
        run_id: str,
        step_id: str,
    ) -> PdfExtraction:
        active = self.connection.execute(
            "SELECT 1 FROM runs JOIN steps USING (run_id) "
            "WHERE runs.run_id = ? AND steps.step_id = ? "
            "AND runs.status = 'running' AND steps.status = 'running'",
            [run_id, step_id],
        ).fetchone()
        if active is None:
            raise RuntimeError("PDF writes require a matching active run and step")
        self._validate_content(content)
        snapshot = self.connection.execute(
            "SELECT source, url, sha256 FROM snapshots WHERE snapshot_id = ?",
            [content.snapshot_id],
        ).fetchone()
        if snapshot != ("pdf", str(httpx.URL(content.url)), content.snapshot_sha256):
            raise DomainError("PDF extraction raw snapshot provenance mismatch")
        for key in finding_keys:
            origin = self.connection.execute(
                "SELECT f.id_documento, d.url_documento FROM findings f "
                "LEFT JOIN LATERAL (SELECT url_documento FROM despesas "
                "WHERE source_file_id = f.source_file_id "
                "AND id_documento = f.id_documento AND record_sha256 = f.record_sha256 "
                "ORDER BY source_record_number LIMIT 1) d ON TRUE "
                "WHERE f.finding_key = ? AND f.status = 'active'",
                [key],
            ).fetchone()
            if origin != (content.id_documento, content.url):
                raise DomainError(
                    "PDF extraction does not match the finding's original URL"
                )
        existing = self._read(
            "SELECT * FROM nf_texto WHERE id_documento = ? AND url = ? "
            "AND snapshot_id = ? AND extractor = ? AND extractor_version = ?",
            [
                content.id_documento,
                content.url,
                content.snapshot_id,
                content.extractor,
                content.extractor_version,
            ],
        )
        if existing is not None and existing.content != content:
            raise DomainError("immutable PDF extraction conflict")
        record = existing or PdfExtraction(
            extraction_id, content, created_at, run_id, step_id
        )
        self.connection.execute("BEGIN TRANSACTION")
        try:
            if existing is None:
                values = asdict(content)
                values["path"] = str(content.path)
                self.connection.execute(
                    "INSERT INTO nf_texto VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        record.extraction_id,
                        *values.values(),
                        created_at,
                        run_id,
                        step_id,
                    ],
                )
            for key in finding_keys:
                self.connection.execute(
                    "INSERT INTO nf_texto_findings VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    [record.extraction_id, key, created_at, run_id, step_id],
                )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return record

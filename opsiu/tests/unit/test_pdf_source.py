"""PDF cache decoding, version changes, and failure ownership boundaries."""

import gzip
import hashlib
import io
from datetime import UTC, datetime
from pathlib import Path

import pypdf
import pytest

from opsiu.adapters.external.pdf import PdfDocumentSource
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import PdfExtraction, Snapshot, SnapshotManifest
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.unit


def blank_pdf() -> bytes:
    output = io.BytesIO()
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    writer.close()
    return output.getvalue()


class SnapshotFixture:
    def __init__(
        self, directory: Path, payload: bytes, encoding: str = "identity"
    ) -> None:
        self.directory = directory
        self.payload = payload
        self.encoding = encoding
        self.records: dict[str, Snapshot] = {}
        self.downloads: list[str] = []

    def download(self, source: str, url: str) -> Snapshot:
        identity = Uuid7Generator().new_id()
        path = self.directory / identity / "payload"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.payload)
        snapshot = Snapshot(
            source,
            identity,
            path,
            SnapshotManifest(
                source,
                identity,
                url,
                200,
                {"content-encoding": self.encoding},
                hashlib.sha256(self.payload).hexdigest(),
                len(self.payload),
                datetime.now(UTC),
                identity,
                identity,
            ),
        )
        self.records[identity] = snapshot
        self.downloads.append(url)
        return snapshot

    def load(self, source: str, snapshot_id: str) -> Snapshot:
        record = self.records[snapshot_id]
        assert record.source == source
        return record

    def list_snapshots(self, source: str) -> tuple[Snapshot, ...]:
        return tuple(
            record for record in self.records.values() if record.source == source
        )

    def load_latest(self, source: str) -> Snapshot | None:
        records = self.list_snapshots(source)
        return records[-1] if records else None


def run_fixture(tmp_path: Path) -> Run:
    return Run(
        ledger=JsonlLedger(tmp_path / "runs"),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="pdf unit",
        arguments={},
        metadata=collect_metadata("settings", tmp_path),
    )


def test_gzip_cache_reuses_verified_bytes_and_reextracts_for_a_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = blank_pdf()
    store = SnapshotFixture(tmp_path / "raw", gzip.compress(original), "gzip")
    run = run_fixture(tmp_path)
    external = tmp_path / "nested" / ".." / "external"
    source = PdfDocumentSource(external, run, store)
    with run:
        with run.step("first"):
            first = source.prepare(42, "https://example.invalid/invoice.pdf")
            previous = PdfExtraction(
                "first", first, run.clock.now(), run.run_id, run.require_step()
            )
        assert first.path == tmp_path / "external" / "pdf" / "42.pdf"
        assert first.path.read_bytes() == original
        assert first.sha256 == hashlib.sha256(original).hexdigest()
        assert first.snapshot_sha256 != first.sha256
        assert first.needs_ocr and first.page_count == 1
        with run.step("cached"):
            assert source.prepare(42, first.url, previous=previous) == first
        monkeypatch.setattr(pypdf, "__version__", "test-next-version")
        newer = PdfDocumentSource(external, run, store)
        with run.step("new extractor"):
            updated = newer.prepare(42, first.url, previous=previous)
    assert store.downloads == [first.url]
    assert updated.path == first.path
    assert updated.snapshot_id == first.snapshot_id
    assert updated.extractor_version == "test-next-version"
    assert first.extractor_version != updated.extractor_version
    assert first.path.read_bytes() == original


def test_changed_original_url_uses_its_own_versioned_path(tmp_path: Path) -> None:
    store = SnapshotFixture(tmp_path / "raw", blank_pdf())
    run = run_fixture(tmp_path)
    source = PdfDocumentSource(tmp_path / "external", run, store)
    with run:
        with run.step("first"):
            first = source.prepare(42, "https://example.invalid/first.pdf")
        with run.step("other URL"):
            second = source.prepare(42, "https://example.invalid/second.pdf")
    assert first.path.name == "42.pdf"
    assert (
        second.path
        == tmp_path
        / "external"
        / "pdf"
        / "versions"
        / "42"
        / f"{second.snapshot_id}.pdf"
    )
    assert first.snapshot_id != second.snapshot_id
    assert first.path.read_bytes() == second.path.read_bytes()
    assert store.downloads == [first.url, second.url]


def test_invalid_pdf_removes_only_its_unpublished_decoded_copy(tmp_path: Path) -> None:
    store = SnapshotFixture(tmp_path / "raw", b"this is not a PDF")
    run = run_fixture(tmp_path)
    with run:
        with run.step("bad PDF"):
            PdfDocumentSource(tmp_path / "external", run, store).prepare(
                42, "https://example.invalid/bad"
            )
    assert run.status == "completed_with_errors"
    assert not (tmp_path / "external" / "pdf" / "42.pdf").exists()
    assert len(store.records) == 1
    assert next(iter(store.records.values())).path.read_bytes() == b"this is not a PDF"


def test_pdf_side_effects_require_an_active_run_step(tmp_path: Path) -> None:
    store = SnapshotFixture(tmp_path / "raw", blank_pdf())
    run = run_fixture(tmp_path)
    source = PdfDocumentSource(tmp_path / "external", run, store)
    with pytest.raises(RuntimeError, match="not active"):
        source.prepare(42, "https://example.invalid/pdf")
    with run:
        with pytest.raises(RuntimeError, match="active run step"):
            source.prepare(42, "https://example.invalid/pdf")
    assert store.downloads == []
    assert not (tmp_path / "external").exists()


def test_cache_cannot_be_reused_for_another_document_or_url(tmp_path: Path) -> None:
    store = SnapshotFixture(tmp_path / "raw", blank_pdf())
    run = run_fixture(tmp_path)
    source = PdfDocumentSource(tmp_path / "external", run, store)
    with run:
        with run.step("first"):
            first = source.prepare(42, "https://example.invalid/pdf")
            previous = PdfExtraction(
                "first", first, run.clock.now(), run.run_id, run.require_step()
            )
        with run.step("wrong cache"):
            with pytest.raises(DomainError, match="requested document and URL"):
                source.prepare(43, first.url, previous=previous)
            with pytest.raises(DomainError, match="requested document and URL"):
                source.prepare(42, "https://example.invalid/other", previous=previous)
    assert len(store.downloads) == 1

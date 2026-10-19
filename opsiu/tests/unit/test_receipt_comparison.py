"""Raw receipt equality requires validated provenance and exact byte comparison."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from opsiu.adapters.files.receipt_comparison import FileReceiptFiles
from opsiu.application.receipt_comparison import (
    ReceiptFingerprint,
    ReceiptReference,
    compare_receipts,
)
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.unit


def receipt(
    external: Path, document: int, data: bytes, *, filename: str | None = None
) -> ReceiptReference:
    path = external / "pdf" / (filename or f"{document}.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return ReceiptReference(
        id_documento=document,
        url=f"https://example.test/receipt/{document}",
        path=path,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        snapshot_id=f"capture-{document}",
        snapshot_sha256="a" * 64,
    )


def test_identical_bytes_across_documents_preserve_origins_and_are_deterministic(
    tmp_path: Path,
) -> None:
    data = b"%PDF-1.7\n" + b"receipt data\x00" * 10000
    one = receipt(tmp_path, 123, data)
    two = receipt(tmp_path, 456, data)
    different = receipt(tmp_path, 789, b"different receipt")
    files = FileReceiptFiles(tmp_path)
    result = compare_receipts([two, different, one], files)
    assert result == compare_receipts([one, different, two], files)
    assert result.failures == ()
    assert result.receipts_checked == 3
    assert result.bytes_verified == len(data) * 2 + len(b"different receipt")
    assert result.byte_comparisons == 1
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.document_ids == (123, 456)
    assert group.receipts == (one, two)
    assert group.sha256 == hashlib.sha256(data).hexdigest()
    assert group.size_bytes == len(data)


def test_equal_size_and_equal_text_do_not_mean_equal_raw_files(tmp_path: Path) -> None:
    one = receipt(tmp_path, 1, b"%PDF-1.7\nInvoice 123\n/Metadata(A)")
    two = receipt(tmp_path, 2, b"%PDF-1.7\nInvoice 123\n/Metadata(B)")
    files = FileReceiptFiles(tmp_path)
    result = compare_receipts([one, two], files)
    assert one.size_bytes == two.size_bytes
    assert result.groups == ()
    assert result.byte_comparisons == 0
    assert not files.identical(one, two)


def test_shared_path_retains_each_document_reference(tmp_path: Path) -> None:
    one = receipt(tmp_path, 1, b"one raw capture")
    two = replace(one, id_documento=2, url="https://example.test/receipt/2")
    result = compare_receipts([one, two], FileReceiptFiles(tmp_path))
    assert result.groups[0].document_ids == (1, 2)
    assert result.groups[0].receipts == (one, two)
    assert result.byte_comparisons == 1


def test_versions_and_repeated_references_of_one_document_do_not_form_a_group(
    tmp_path: Path,
) -> None:
    one = receipt(tmp_path, 1, b"one raw capture")
    version = receipt(tmp_path, 1, b"one raw capture", filename="versions/1/new.pdf")
    version = replace(version, snapshot_id="new-capture")
    result = compare_receipts([one, one, version], FileReceiptFiles(tmp_path))
    assert result.groups == ()
    assert result.receipts_checked == 2
    assert result.byte_comparisons == 0


@pytest.mark.parametrize("problem", ["missing", "hash", "size", "zero-id"])
def test_invalid_capture_is_explicit_failure_never_an_identical_group(
    tmp_path: Path, problem: str
) -> None:
    one = receipt(tmp_path, 1, b"valid receipt")
    two = receipt(tmp_path, 2, b"valid receipt")
    if problem == "missing":
        two.path.unlink()
    elif problem == "hash":
        two.path.write_bytes(b"other receipt")
    elif problem == "size":
        two.path.write_bytes(b"too big for recorded metadata")
    else:
        two = replace(two, id_documento=0)
    result = compare_receipts([one, two], FileReceiptFiles(tmp_path))
    assert result.groups == ()
    assert result.receipts_checked == 1
    assert len(result.failures) == 1
    assert result.failures[0].receipt == two
    assert result.failures[0].error


@pytest.mark.parametrize("problem", ["outside", "symlink"])
def test_only_canonical_regular_files_inside_pdf_cache_are_read(
    tmp_path: Path, problem: str
) -> None:
    one = receipt(tmp_path, 1, b"valid receipt")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"valid receipt")
    if problem == "symlink":
        link = tmp_path / "pdf" / "linked.pdf"
        link.symlink_to(outside)
        outside = link
    bad = replace(one, path=outside)
    result = compare_receipts([bad], FileReceiptFiles(tmp_path))
    assert result.receipts_checked == 0
    assert len(result.failures) == 1
    assert "Caminho" in result.failures[0].error


@pytest.mark.parametrize("limit", ["receipts", "bytes"])
def test_limits_abort_before_any_file_reads(tmp_path: Path, limit: str) -> None:
    one = receipt(tmp_path, 1, b"receipt")
    two = receipt(tmp_path, 2, b"receipt")
    one.path.unlink()
    two.path.unlink()
    with pytest.raises(DomainError, match="limite"):
        compare_receipts(
            [one, two],
            FileReceiptFiles(tmp_path),
            max_receipts=1 if limit == "receipts" else 2,
            max_total_bytes=1 if limit == "bytes" else 100,
        )


class CollidingFiles:
    """Even equal verified hashes must pass the byte comparison port."""

    def verify(self, receipt: ReceiptReference) -> ReceiptFingerprint:
        return ReceiptFingerprint(receipt.size_bytes, receipt.sha256)

    def identical(self, left: ReceiptReference, right: ReceiptReference) -> bool:
        return left.id_documento % 2 == right.id_documento % 2


def test_equal_hash_still_requires_bytes_and_partitioning(tmp_path: Path) -> None:
    references = [receipt(tmp_path, doc, b"receipt") for doc in range(1, 5)]
    result = compare_receipts(references, CollidingFiles())
    assert [group.document_ids for group in result.groups] == [(1, 3), (2, 4)]
    assert result.byte_comparisons == 4


class MutatingFiles(FileReceiptFiles):
    def identical(self, left: ReceiptReference, right: ReceiptReference) -> bool:
        right.path.write_bytes(b"tampered")
        return super().identical(left, right)


def test_file_changed_after_verification_aborts_comparison(tmp_path: Path) -> None:
    one = receipt(tmp_path, 1, b"original")
    two = receipt(tmp_path, 2, b"original")
    with pytest.raises(DomainError, match="Bytes"):
        compare_receipts([one, two], MutatingFiles(tmp_path))

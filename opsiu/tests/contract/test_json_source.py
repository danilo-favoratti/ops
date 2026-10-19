"""Streaming source shape, bounded I/O, hashes, and completion metadata."""

import codecs
import hashlib
import io
import json
from pathlib import Path
from typing import BinaryIO

import pytest

from opsiu.adapters.files.json_source import JsonExpenseSource, JsonSourceError
from opsiu.application.ports import ExpenseSource
from opsiu.domain.record_hash import record_sha256

pytestmark = pytest.mark.contract
FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures/mini_dataset/five_records.json"
)


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 65536])
def test_five_records_match_json_loads_and_hash_original_bytes(chunk_size: int) -> None:
    original = FIXTURE.read_bytes()
    expected = json.loads(original)["dados"]
    source: ExpenseSource = JsonExpenseSource(FIXTURE, chunk_size=chunk_size)
    assert source.metadata is None

    records = list(source)

    assert len(records) == 5
    assert [raw for raw, _ in records] == expected
    assert [digest for _, digest in records] == [record_sha256(raw) for raw in expected]
    assert source.metadata is not None
    assert source.metadata.source_file == str(FIXTURE)
    assert source.metadata.source_sha256 == hashlib.sha256(original).hexdigest()
    assert source.metadata.size_bytes == len(original)


def test_bom_and_trailing_whitespace_are_included_in_file_hash(tmp_path: Path) -> None:
    original = codecs.BOM_UTF8 + FIXTURE.read_bytes() + b" \r\n\t"
    fixture = tmp_path / "bom.json"
    fixture.write_bytes(original)
    source = JsonExpenseSource(fixture, chunk_size=1)

    assert len(list(source)) == 5
    assert source.metadata is not None
    assert source.metadata.source_sha256 == hashlib.sha256(original).hexdigest()
    assert source.metadata.size_bytes == len(original)


def test_valid_empty_array_and_unrelated_envelope_metadata(tmp_path: Path) -> None:
    fixture = tmp_path / "empty.json"
    fixture.write_text(
        '{"before":{"nested":[1,{"dados":[true]}]},'
        '"dados":[],"dados.item":{"not":"a record"},"after":[null]}',
        encoding="utf-8",
    )
    source = JsonExpenseSource(fixture, chunk_size=2)

    assert list(source) == []
    assert source.metadata is not None


@pytest.mark.parametrize(
    "contents",
    [
        b"",
        b"[]",
        b"{}",
        b'{"other":[]}',
        b'{"dados":{}}',
        b'{"dados":null}',
        b'{"dados":[1]}',
        b'{"dados":[null]}',
        b'{"dados":[[]]}',
        b'{"dados":[{"x":1},',
        b'{"dados":[{"x":1}]} trailing',
        b'{"dados":[]} {}',
        b'{"dados":[],"dados":[]}',
    ],
)
def test_invalid_shape_or_incomplete_document_never_exposes_complete_metadata(
    tmp_path: Path, contents: bytes
) -> None:
    fixture = tmp_path / "invalid.json"
    fixture.write_bytes(contents)
    source = JsonExpenseSource(fixture, chunk_size=3)

    with pytest.raises(JsonSourceError):
        list(source)
    assert source.metadata is None


class TrackingStream(io.BytesIO):
    def __init__(self, payload: bytes, maximum_read: int) -> None:
        super().__init__(payload)
        self.maximum_read = maximum_read
        self.bytes_read = 0
        self.read_sizes: list[int] = []

    def read(self, size: int | None = -1) -> bytes:
        assert size is not None and 0 < size <= self.maximum_read
        self.read_sizes.append(size)
        block = super().read(size)
        self.bytes_read += len(block)
        return block


def test_reads_are_bounded_single_pass_and_partial_iteration_closes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b'{"dados":[' + b'{"idDocumento":1},' * 5000 + b'{"idDocumento":2}]}'
    streams: list[TrackingStream] = []

    def tracked_open(path: Path, *args: object, **kwargs: object) -> BinaryIO:
        stream = TrackingStream(payload, 32)
        streams.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    source = JsonExpenseSource(tmp_path / "streamed.json", chunk_size=32)
    iterator = iter(source)
    next(iterator)
    assert 0 < streams[0].bytes_read < len(payload)
    assert source.metadata is None
    iterator.close()
    assert streams[0].closed
    assert source.metadata is None

    assert sum(1 for _ in source) == 5001
    assert len(streams) == 2
    assert streams[1].closed
    assert streams[1].bytes_read == len(payload)
    assert source.metadata is not None
    assert source.metadata.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert source.metadata.size_bytes == len(payload)


def test_error_closes_file_and_reiteration_resets_previous_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payloads = [b'{"dados":[]}', b'{"dados":[{"x":1},']
    streams: list[TrackingStream] = []

    def tracked_open(path: Path, *args: object, **kwargs: object) -> BinaryIO:
        stream = TrackingStream(payloads[len(streams)], 4)
        streams.append(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    source = JsonExpenseSource(tmp_path / "changed.json", chunk_size=4)
    assert list(source) == []
    assert source.metadata is not None
    with pytest.raises(JsonSourceError):
        list(source)

    assert source.metadata is None
    assert all(stream.closed for stream in streams)


def test_metadata_is_unavailable_until_iterator_is_fully_exhausted() -> None:
    source = JsonExpenseSource(FIXTURE)
    iterator = iter(source)
    for _ in range(5):
        next(iterator)
        assert source.metadata is None
    with pytest.raises(StopIteration):
        next(iterator)
    assert source.metadata is not None


def test_concurrent_iterator_is_rejected_without_closing_original() -> None:
    source = JsonExpenseSource(FIXTURE, chunk_size=1)
    first = iter(source)
    next(first)
    second = iter(source)
    with pytest.raises(RuntimeError, match="active iterator"):
        next(second)
    assert len(list(first)) == 4
    assert source.metadata is not None

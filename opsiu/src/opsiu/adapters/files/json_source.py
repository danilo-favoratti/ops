"""Bounded, single-pass reading and hashing of CEAP yearly JSON files."""

from __future__ import annotations

import codecs
import hashlib
from collections.abc import Generator, Iterator
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, cast

import ijson
from ijson.common import ObjectBuilder

from opsiu.application.ports import ExpenseSourceMetadata
from opsiu.domain.record_hash import record_sha256

type Parser = Iterator[tuple[str, object]]


class JsonSourceError(ValueError):
    """A source is malformed or does not contain the expected dados array."""


class _HashingReader:
    """Hash every original byte once, retaining at most one bounded read chunk."""

    def __init__(self, source: BinaryIO, chunk_size: int) -> None:
        self.source = source
        self.chunk_size = chunk_size
        self.sha256 = hashlib.sha256()
        self.size_bytes = 0
        self.eof = False
        self._started = False
        self._prefix = b""

    def _read_raw(self, size: int) -> bytes:
        block = self.source.read(size)
        self.sha256.update(block)
        self.size_bytes += len(block)
        if not block:
            self.eof = True
        return block

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        size = self.chunk_size if size < 0 else min(size, self.chunk_size)
        if not self._started:
            self._started = True
            prefix = b""
            while len(prefix) < 3 and not self.eof:
                prefix += self._read_raw(min(3 - len(prefix), self.chunk_size))
            # The parser ignores a UTF-8 BOM, but the original-file hash includes it.
            self._prefix = b"" if prefix == codecs.BOM_UTF8 else prefix
        block, self._prefix = self._prefix[:size], self._prefix[size:]
        if len(block) < size and not self.eof:
            block += self._read_raw(size - len(block))
        return block


def _next(parser: Parser) -> tuple[str, object]:
    try:
        return next(parser)
    except StopIteration as error:
        raise JsonSourceError("unexpected end of CEAP JSON") from error


def _skip_value(parser: Parser, first: tuple[str, object]) -> None:
    depth = 1 if first[0] in {"start_map", "start_array"} else 0
    while depth:
        event, _ = _next(parser)
        if event in {"start_map", "start_array"}:
            depth += 1
        elif event in {"end_map", "end_array"}:
            depth -= 1


def _record(parser: Parser) -> dict[str, object]:
    builder = ObjectBuilder()
    builder.event("start_map", None)
    depth = 1
    while depth:
        event, value = _next(parser)
        # ijson's default preserves arbitrary-size integers. Convert fractional
        # Decimal tokens to float to match json.loads and the shared raw hash.
        if event == "number" and isinstance(value, Decimal):
            value = float(value)
        builder.event(event, value)
        if event in {"start_map", "start_array"}:
            depth += 1
        elif event in {"end_map", "end_array"}:
            depth -= 1
    if not isinstance(builder.value, dict):
        raise JsonSourceError("each dados item must be an object")
    return cast(dict[str, object], builder.value)


def _records(parser: Parser) -> Iterator[dict[str, object]]:
    if _next(parser)[0] != "start_map":
        raise JsonSourceError("the CEAP JSON root must be an object")
    found = False
    while True:
        event, key = _next(parser)
        if event == "end_map":
            break
        if event != "map_key":
            raise JsonSourceError("the CEAP JSON root must contain named fields")
        first = _next(parser)
        if key != "dados":
            _skip_value(parser, first)
            continue
        if found:
            raise JsonSourceError("the CEAP JSON root contains duplicate dados fields")
        found = True
        if first[0] != "start_array":
            raise JsonSourceError("dados must be an array")
        while True:
            event, _ = _next(parser)
            if event == "end_array":
                break
            if event != "start_map":
                raise JsonSourceError("each dados item must be an object")
            yield _record(parser)
    if not found:
        raise JsonSourceError("the CEAP JSON root is missing dados")
    # Exhaust the parser to validate trailing input and read all remaining bytes.
    if next(parser, None) is not None:
        raise JsonSourceError("unexpected data after the CEAP JSON root")


class JsonExpenseSource:
    """Stream one raw record at a time; metadata describes only completed passes.

    A new pass clears metadata when iteration starts. Close an abandoned iterator
    explicitly to release its file immediately. Concurrent passes are rejected.
    """

    def __init__(self, path: Path, *, chunk_size: int = 65536) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.path = path
        self.chunk_size = chunk_size
        self._metadata: ExpenseSourceMetadata | None = None
        self._active = False

    @property
    def metadata(self) -> ExpenseSourceMetadata | None:
        return self._metadata

    def __iter__(self) -> Generator[tuple[dict[str, object], str], None, None]:
        if self._active:
            raise RuntimeError("this source already has an active iterator")
        self._metadata = None
        self._active = True
        try:
            with self.path.open("rb") as source:
                reader = _HashingReader(source, self.chunk_size)
                parser = cast(
                    Parser, ijson.basic_parse(reader, buf_size=self.chunk_size)
                )
                try:
                    for raw in _records(parser):
                        yield raw, record_sha256(raw)
                except ijson.JSONError as error:
                    raise JsonSourceError(f"invalid CEAP JSON: {error}") from error
                if not reader.eof:
                    raise JsonSourceError(
                        "the parser did not reach the end of the source"
                    )
                self._metadata = ExpenseSourceMetadata(
                    str(self.path), reader.sha256.hexdigest(), reader.size_bytes
                )
        finally:
            self._active = False

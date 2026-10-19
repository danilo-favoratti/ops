"""Stream and verify immutable cached receipts before comparing their raw bytes."""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from opsiu.application.receipt_comparison import ReceiptFingerprint, ReceiptReference
from opsiu.domain.errors import DomainError

_CHUNK_BYTES = 64 * 1024


class FileReceiptFiles:
    def __init__(self, external_dir: Path) -> None:
        self.directory = external_dir.resolve() / "pdf"

    @contextmanager
    def _open(self, receipt: ReceiptReference) -> Iterator[BinaryIO]:
        receipt.validate()
        path = receipt.path
        if (
            not path.is_absolute()
            or path.resolve() != path
            or not path.is_relative_to(self.directory)
        ):
            raise DomainError("Caminho do comprovante fora do cache permitido")
        try:
            if not path.is_file():
                raise DomainError("Arquivo do comprovante ausente ou não regular")
            with path.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise DomainError("Arquivo do comprovante não regular")
                if metadata.st_size != receipt.size_bytes:
                    raise DomainError(
                        "Tamanho do comprovante diverge da origem registrada"
                    )
                yield stream
        except OSError as error:
            raise DomainError(f"Falha ao ler comprovante: {error}") from error

    @staticmethod
    def _validate_read(receipt: ReceiptReference, size: int, digest: str) -> None:
        if size != receipt.size_bytes or digest != receipt.sha256:
            raise DomainError("Bytes do comprovante divergem da origem registrada")

    def verify(self, receipt: ReceiptReference) -> ReceiptFingerprint:
        digest = hashlib.sha256()
        size = 0
        with self._open(receipt) as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > receipt.size_bytes:
                    raise DomainError("Tamanho do comprovante mudou durante a leitura")
                digest.update(chunk)
        sha256 = digest.hexdigest()
        self._validate_read(receipt, size, sha256)
        return ReceiptFingerprint(size, sha256)

    def identical(self, left: ReceiptReference, right: ReceiptReference) -> bool:
        left_digest = hashlib.sha256()
        right_digest = hashlib.sha256()
        left_size = right_size = 0
        identical = True
        with self._open(left) as left_file, self._open(right) as right_file:
            while True:
                left_chunk = left_file.read(_CHUNK_BYTES)
                right_chunk = right_file.read(_CHUNK_BYTES)
                if not left_chunk and not right_chunk:
                    break
                left_size += len(left_chunk)
                right_size += len(right_chunk)
                if left_size > left.size_bytes or right_size > right.size_bytes:
                    raise DomainError("Tamanho do comprovante mudou durante a leitura")
                left_digest.update(left_chunk)
                right_digest.update(right_chunk)
                if left_chunk != right_chunk:
                    identical = False
        self._validate_read(left, left_size, left_digest.hexdigest())
        self._validate_read(right, right_size, right_digest.hexdigest())
        return identical

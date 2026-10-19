"""Stream Receita's headerless Latin-1 files, retaining selected CNPJ roots."""

import csv
import io
import re
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import PurePosixPath
from typing import BinaryIO, cast

from opsiu.application.ports import (
    CnpjCompany,
    CnpjEstablishment,
    CnpjFamily,
    CnpjOrigin,
    CnpjPartner,
    CnpjProgress,
    CnpjRecord,
    CnpjSnapshot,
    CnpjSnapshotFile,
)
from opsiu.domain.errors import DomainError

_WIDTHS: dict[CnpjFamily, int] = {"empresas": 7, "socios": 11, "estabelecimentos": 30}
_SUFFIXES: dict[str, CnpjFamily] = {
    ".EMPRECSV": "empresas",
    ".SOCIOCSV": "socios",
    ".ESTABELE": "estabelecimentos",
}


class CnpjBulkSource:
    def __init__(self, *, progress_interval: int = 1_000_000) -> None:
        if progress_interval < 1:
            raise ValueError("progress interval must be positive")
        self.progress_interval = progress_interval

    def records(
        self, snapshot: CnpjSnapshot, roots: frozenset[str]
    ) -> Iterator[CnpjRecord | CnpjProgress]:
        scanned = 0
        for file in snapshot.manifest.files:
            with ExitStack() as stack:
                raw = stack.enter_context(
                    (snapshot.path / "files" / file.name).open("rb")
                )
                streams: list[tuple[str, BinaryIO]] = []
                if zipfile.is_zipfile(raw):
                    archive = stack.enter_context(zipfile.ZipFile(raw))
                    members = []
                    for member in archive.infolist():
                        path = PurePosixPath(member.filename)
                        if (
                            path.is_absolute()
                            or ".." in path.parts
                            or "\\" in member.filename
                        ):
                            raise DomainError(
                                "Receita ZIP contains an unsafe member path"
                            )
                        if member.is_dir():
                            continue
                        suffix = path.suffix.upper()
                        family = _SUFFIXES.get(suffix)
                        if family is not None and family != file.family:
                            raise DomainError(
                                "Receita ZIP contains another file family"
                            )
                        if family is not None or suffix == ".CSV":
                            members.append(member)
                    if not members or len({m.filename for m in members}) != len(
                        members
                    ):
                        raise DomainError(
                            "Receita ZIP has missing or duplicate CSV members"
                        )
                    # Open only the current member, so descriptors remain bounded too.
                    for member in sorted(members, key=lambda value: value.filename):
                        with archive.open(member) as contents:
                            for number, row in self._rows(
                                cast(BinaryIO, contents), file
                            ):
                                scanned += 1
                                if row[0].strip() in roots:
                                    yield self._record(
                                        file, member.filename, number, row
                                    )
                                if scanned % self.progress_interval == 0:
                                    yield CnpjProgress(
                                        file.family, file.name, member.filename, scanned
                                    )
                else:
                    raw.seek(0)
                    streams.append((file.name, raw))
                    for member_name, stream in streams:
                        for number, row in self._rows(stream, file):
                            scanned += 1
                            # Alphanumeric roots outside the numeric CEAP set are
                            # retained raw, never stripped into a different identifier.
                            if row[0].strip() in roots:
                                yield self._record(file, member_name, number, row)
                            if scanned % self.progress_interval == 0:
                                yield CnpjProgress(
                                    file.family, file.name, member_name, scanned
                                )

    def _rows(
        self, stream: BinaryIO, file: CnpjSnapshotFile
    ) -> Iterator[tuple[int, list[str]]]:
        prefix = stream.read(3)
        stream.seek(0)
        encoding = "utf-8-sig" if prefix == b"\xef\xbb\xbf" else "latin-1"
        with io.TextIOWrapper(stream, encoding=encoding, newline="") as text:
            for number, row in enumerate(
                csv.reader(text, delimiter=";", strict=True), 1
            ):
                if len(row) != _WIDTHS[file.family]:
                    raise DomainError(
                        f"Receita {file.family} row {number} has {len(row)} columns; "
                        f"expected {_WIDTHS[file.family]}"
                    )
                yield number, row

    def _record(
        self, file: CnpjSnapshotFile, member: str, number: int, row: list[str]
    ) -> CnpjRecord:
        root = row[0].strip()
        if re.fullmatch(r"[0-9]{8}", root) is None:
            raise DomainError("selected Receita root must contain eight digits")
        origin = CnpjOrigin(file.name, file.sha256, member, number)
        if file.family == "empresas":
            name, nature = row[1].strip(), row[2].strip()
            if not name or re.fullmatch(r"[0-9]{4}", nature) is None:
                raise DomainError("Receita company name or legal nature is invalid")
            return CnpjCompany(root, name, nature, origin)
        if file.family == "socios":
            kind, name, qualification = row[1].strip(), row[2].strip(), row[4].strip()
            if kind not in {"1", "2", "3"} or not name or not qualification:
                raise DomainError(
                    "Receita partner type, name, or qualification is invalid"
                )
            return CnpjPartner(root, int(kind), name, qualification, origin)
        order, digits, cnae = row[1].strip().upper(), row[2].strip(), row[11].strip()
        if (
            re.fullmatch(r"[A-Z0-9]{4}", order) is None
            or re.fullmatch(r"[0-9]{2}", digits) is None
        ):
            raise DomainError("Receita establishment order or check digits are invalid")
        if cnae and re.fullmatch(r"[0-9]{7}", cnae) is None:
            raise DomainError("Receita primary CNAE must contain seven digits")
        return CnpjEstablishment(root, root + order + digits, cnae or None, origin)

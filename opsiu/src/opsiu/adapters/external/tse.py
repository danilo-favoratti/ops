"""Stream official semicolon candidate CSVs from validated local snapshots."""

import csv
import gzip
import io
import re
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import datetime
from pathlib import PurePosixPath
from typing import BinaryIO, cast

from opsiu.application.ports import Snapshot, TseCandidate
from opsiu.domain.errors import DomainError
from opsiu.domain.policies import COTAS_UF, ELECTION_DATES
from opsiu.domain.text import normalize_name

_FIELDS = {
    "ANO_ELEICAO",
    "SQ_CANDIDATO",
    "NR_CPF_CANDIDATO",
    "NM_CANDIDATO",
    "SG_UF",
    "DS_CARGO",
    "DT_ELEICAO",
    "NR_TURNO",
    "CD_TIPO_ELEICAO",
}
_STATES = frozenset({*(quota.uf for quota in COTAS_UF), "BR"})
_MISSING = frozenset({"", "-1", "-3", "-4", "#NULO#", "#NE#", "NULO", "NA"})


def _cpf(value: str) -> str | None:
    value = value.strip()
    if value.upper() in _MISSING or "*" in value:
        return None
    digits = re.sub(r"[.\-\s]", "", value)
    if re.fullmatch(r"[0-9]{11}", digits) is None:
        raise DomainError("TSE CPF must contain eleven digits or a missing sentinel")
    if len(set(digits)) == 1:
        return None
    return digits


def _members(archive: zipfile.ZipFile, year: int) -> tuple[zipfile.ZipInfo, ...]:
    selected: list[tuple[str, zipfile.ZipInfo]] = []
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
            raise DomainError("TSE ZIP contains an unsafe member path")
        if member.is_dir() or not member.filename.lower().endswith(".csv"):
            continue
        match = re.fullmatch(
            rf"consulta_cand_{year}_(BRASIL|[A-Z]{{2}})\.csv",
            path.name,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        region = match[1].upper()
        if region != "BRASIL" and region not in _STATES:
            raise DomainError("TSE ZIP has an unknown state member")
        selected.append((region, member))
    national = [member for region, member in selected if region == "BRASIL"]
    if len(national) > 1:
        raise DomainError("TSE ZIP contains duplicate national CSVs")
    if national:
        return tuple(national)
    if not selected:
        raise DomainError("TSE ZIP has no candidate CSV for the requested year")
    if len({region for region, _ in selected}) != len(selected):
        raise DomainError("TSE ZIP contains duplicate state CSVs")
    return tuple(member for _, member in sorted(selected, key=lambda item: item[0]))


class TseCandidateSource:
    def candidates(self, snapshot: Snapshot, year: int) -> Iterator[TseCandidate]:
        if year not in ELECTION_DATES or snapshot.source != f"tse_{year}":
            raise DomainError("TSE snapshot source does not match a supported year")
        encoding = snapshot.manifest.headers.get("content-encoding", "identity")
        with ExitStack() as stack:
            raw = stack.enter_context(snapshot.path.open("rb"))
            stream: BinaryIO = raw
            if encoding.lower().strip() in {"gzip", "x-gzip"}:
                stream = cast(BinaryIO, stack.enter_context(gzip.GzipFile(fileobj=raw)))
            elif encoding.lower().strip() not in {"", "identity"}:
                raise DomainError(f"unsupported TSE content encoding: {encoding}")
            if zipfile.is_zipfile(stream):
                archive = stack.enter_context(zipfile.ZipFile(stream))
                for member in _members(archive, year):
                    with archive.open(member) as contents:
                        yield from self._csv(
                            cast(BinaryIO, contents), member.filename, year
                        )
            else:
                stream.seek(0)
                yield from self._csv(stream, "payload", year)

    def _csv(self, stream: BinaryIO, member: str, year: int) -> Iterator[TseCandidate]:
        # Official exports use Latin-1; a UTF-8 BOM explicitly selects UTF-8.
        prefix = stream.read(3)
        stream.seek(0)
        encoding = "utf-8-sig" if prefix == b"\xef\xbb\xbf" else "latin-1"
        with io.TextIOWrapper(stream, encoding=encoding, newline="") as text:
            reader = csv.DictReader(text, delimiter=";", strict=True)
            fields = reader.fieldnames
            if fields is None or len(fields) != len(set(fields)):
                raise DomainError("TSE CSV requires unique named headers")
            if not _FIELDS <= set(fields):
                raise DomainError("TSE CSV is missing required candidate columns")
            for number, row in enumerate(reader, start=1):
                if None in row or any(value is None for value in row.values()):
                    raise DomainError("TSE CSV row does not match its headers")
                if row["ANO_ELEICAO"].strip() != str(year):
                    raise DomainError("TSE candidate election year does not match")
                try:
                    election_date = datetime.strptime(
                        row["DT_ELEICAO"].strip(), "%d/%m/%Y"
                    ).date()
                    turn = int(row["NR_TURNO"])
                    election_type = int(row["CD_TIPO_ELEICAO"])
                except ValueError as error:
                    raise DomainError(
                        "TSE election date, turn, or type is invalid"
                    ) from error
                if (
                    election_date != ELECTION_DATES[year]
                    or turn != 1
                    or election_type != 2
                ):
                    continue
                identity = row["SQ_CANDIDATO"].strip()
                name = normalize_name(row["NM_CANDIDATO"])
                state = row["SG_UF"].strip().upper()
                role = row["DS_CARGO"].strip()
                if not identity.isascii() or not identity.isdigit():
                    raise DomainError("TSE candidate ID must contain ASCII digits")
                if not name or state not in _STATES or not role:
                    raise DomainError("TSE candidate name, state, or role is invalid")
                yield TseCandidate(
                    candidate_id=identity,
                    year=year,
                    cpf=_cpf(row["NR_CPF_CANDIDATO"]),
                    nome_normalizado=name,
                    uf=state,
                    cargo=role,
                    source_member=member,
                    source_record_number=number,
                )

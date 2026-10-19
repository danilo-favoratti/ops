"""Candidate CSV decoding, ZIP selection, election filters, and name matching."""

import gzip
import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from opsiu.adapters.external.tse import TseCandidateSource
from opsiu.application.ports import Snapshot, SnapshotManifest
from opsiu.domain.errors import DomainError
from opsiu.domain.text import normalize_name, normalized_name_sql

pytestmark = pytest.mark.unit
FIXTURE = Path(__file__).parents[1] / "fixtures" / "tse" / "candidates_2024.csv"


def snapshot(tmp_path: Path, data: bytes, encoding: str = "identity") -> Snapshot:
    path = tmp_path / "payload"
    path.write_bytes(data)
    identity = "00000000-0000-7000-8000-000000000001"
    return Snapshot(
        "tse_2024",
        identity,
        path,
        SnapshotManifest(
            "tse_2024",
            identity,
            "https://example.invalid/tse.zip",
            200,
            {"content-encoding": encoding},
            hashlib.sha256(data).hexdigest(),
            len(data),
            datetime(2026, 1, 1, tzinfo=UTC),
            identity,
            identity,
        ),
    )


def zip_bytes(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)
    return output.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "latin-1"])
def test_csv_encoding_and_first_regular_round_filter(
    tmp_path: Path, encoding: str
) -> None:
    text = FIXTURE.read_text(encoding="utf-8-sig")
    loaded = tuple(
        TseCandidateSource().candidates(snapshot(tmp_path, text.encode(encoding)), 2024)
    )
    assert len(loaded) == 2
    assert loaded[0].candidate_id == "1001"
    assert loaded[0].cpf == "01234567890"
    assert loaded[0].nome_normalizado == "JOSE DA SILVA"
    assert loaded[1].cpf is None
    assert loaded[1].nome_normalizado == "ANA MARIA DE SA"
    assert loaded[1].source_record_number == 2


def test_zip_prefers_national_over_state_and_retains_raw_gzip(tmp_path: Path) -> None:
    source = FIXTURE.read_bytes()
    data = gzip.compress(
        zip_bytes(
            {
                "consulta_cand_2024_BRASIL.csv": source,
                "consulta_cand_2024_SP.csv": b"ignored malformed state copy",
                "leiame.pdf": b"ignored documentation",
            }
        )
    )
    raw = snapshot(tmp_path, data, "gzip")
    loaded = tuple(TseCandidateSource().candidates(raw, 2024))
    assert len(loaded) == 2
    assert {row.source_member for row in loaded} == {"consulta_cand_2024_BRASIL.csv"}
    assert raw.path.read_bytes() == data


def test_zip_combines_state_and_federal_scope_without_national(tmp_path: Path) -> None:
    source = FIXTURE.read_bytes()
    raw = snapshot(
        tmp_path,
        zip_bytes(
            {
                "consulta_cand_2024_SP.csv": source,
                "consulta_cand_2024_BR.csv": source,
            }
        ),
    )
    loaded = tuple(TseCandidateSource().candidates(raw, 2024))
    assert len(loaded) == 4
    assert [row.source_member for row in loaded] == [
        "consulta_cand_2024_BR.csv",
        "consulta_cand_2024_BR.csv",
        "consulta_cand_2024_SP.csv",
        "consulta_cand_2024_SP.csv",
    ]


@pytest.mark.parametrize(
    "members",
    [
        {"../escape.csv": b"bad"},
        {"/absolute.csv": b"bad"},
        {"nested\\escape.csv": b"bad"},
        {"consulta_cand_2022_BRASIL.csv": b"wrong year"},
        {"consulta_cand_2024_ZZ.csv": b"unknown state"},
        {
            "a/consulta_cand_2024_SP.csv": b"duplicate state",
            "b/consulta_cand_2024_SP.csv": b"duplicate state",
        },
    ],
)
def test_invalid_zip_members_never_extract_files(
    tmp_path: Path, members: dict[str, bytes]
) -> None:
    raw = snapshot(tmp_path, zip_bytes(members))
    with pytest.raises(DomainError):
        tuple(TseCandidateSource().candidates(raw, 2024))
    assert list(tmp_path.iterdir()) == [raw.path]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("ANO_ELEICAO", "WRONG_HEADER"),
        ("2024;1001", "2026;1001"),
        ("01234567890", "invalid-cpf"),
        ("06/10/2024", "not-a-date"),
        (";SP;", ";XX;"),
        (";1001;", ";not-an-id;"),
    ],
)
def test_malformed_candidate_data_is_not_silently_ignored(
    tmp_path: Path, old: str, new: str
) -> None:
    data = FIXTURE.read_text(encoding="utf-8-sig").replace(old, new)
    with pytest.raises(DomainError):
        tuple(
            TseCandidateSource().candidates(
                snapshot(tmp_path, data.encode("utf-8-sig")), 2024
            )
        )


def test_successful_empty_election_filter_and_unsupported_content_encoding(
    tmp_path: Path,
) -> None:
    data = FIXTURE.read_text(encoding="utf-8-sig").replace(";1;2", ";1;1")
    raw = snapshot(tmp_path, data.encode("utf-8-sig"))
    assert tuple(TseCandidateSource().candidates(raw, 2024)) == ()
    with pytest.raises(DomainError, match="content encoding"):
        tuple(
            TseCandidateSource().candidates(
                snapshot(tmp_path, b"compressed", "br"), 2024
            )
        )


@pytest.mark.parametrize(
    "name",
    ["  João-d'Ávila  ", "ÁNA\tMARIA", "Jose\u0301", "João 123", "Ł ß ﬁ"],
)
def test_pure_and_sql_name_normalization_agree(name: str) -> None:
    with duckdb.connect() as connection:
        result = connection.execute(
            f"SELECT {normalized_name_sql('name')} FROM (SELECT ? AS name)", [name]
        ).fetchone()
    assert result == (normalize_name(name),)

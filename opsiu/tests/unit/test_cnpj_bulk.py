"""Bounded Receita filtering, official ZIP layouts, and row provenance."""

import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opsiu.adapters.external.cnpj_bulk import CnpjBulkSource
from opsiu.application.ports import (
    CnpjCompany,
    CnpjEstablishment,
    CnpjFamily,
    CnpjPartner,
    CnpjProgress,
    CnpjSnapshot,
    CnpjSnapshotFile,
    CnpjSnapshotManifest,
)
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.unit
FIXTURES = Path(__file__).parents[1] / "fixtures" / "cnpj"
FAMILIES: tuple[tuple[str, CnpjFamily, str], ...] = (
    ("Empresas0", "empresas", "K3241.TEST.EMPRECSV"),
    ("Estabelecimentos0", "estabelecimentos", "K3241.TEST.ESTABELE"),
    ("Socios0", "socios", "K3241.TEST.SOCIOCSV"),
)


def make_snapshot(
    tmp_path: Path, *, zipped: bool = False, unsafe_member: str | None = None
) -> CnpjSnapshot:
    directory = tmp_path / "files"
    directory.mkdir()
    files = []
    for basename, family, member in FAMILIES:
        contents = (FIXTURES / f"{basename}.csv").read_bytes()
        if zipped:
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr(unsafe_member or member, contents)
            contents = output.getvalue()
        name = basename + (".zip" if zipped else ".csv")
        (directory / name).write_bytes(contents)
        files.append(
            CnpjSnapshotFile(
                name, family, hashlib.sha256(contents).hexdigest(), len(contents), name
            )
        )
    identity = "00000000-0000-7000-8000-000000000001"
    manifest = CnpjSnapshotManifest(
        identity,
        "original",
        "0" * 64,
        sum(file.size_bytes for file in files),
        datetime(2026, 1, 1, tzinfo=UTC),
        identity,
        identity,
        tuple(files),
    )
    return CnpjSnapshot(identity, tmp_path, manifest)


@pytest.mark.parametrize("zipped", [False, True])
def test_official_layouts_filter_roots_preserve_alpha_branches_and_report_progress(
    tmp_path: Path, zipped: bool
) -> None:
    snapshot = make_snapshot(tmp_path, zipped=zipped)
    events = tuple(
        CnpjBulkSource(progress_interval=2).records(snapshot, frozenset({"00000001"}))
    )
    records = [item for item in events if not isinstance(item, CnpjProgress)]
    assert len(records) == 5
    assert all(item.cnpj_root == "00000001" for item in records)
    assert isinstance(records[0], CnpjCompany)
    assert records[0].natureza_juridica == "2062"
    branches = [item for item in records if isinstance(item, CnpjEstablishment)]
    assert [(item.cnpj, item.cnae_principal) for item in branches] == [
        ("00000001000100", "4711302"),
        ("00000001E08G12", "5611201"),
    ]
    assert [item.tipo_socio for item in records if isinstance(item, CnpjPartner)] == [
        2,
        1,
    ]
    assert [item.scanned_rows for item in events if isinstance(item, CnpjProgress)] == [
        2,
        4,
        6,
        8,
    ]
    assert records[0].origin.source_record_number == 1
    assert records[0].origin.source_sha256 == snapshot.manifest.files[0].sha256
    expected_member = "K3241.TEST.EMPRECSV" if zipped else "Empresas0.csv"
    assert records[0].origin.source_member == expected_member


@pytest.mark.parametrize("member", ["../escape.csv", "/absolute.csv", "a\\bad.csv"])
def test_zip_member_paths_are_rejected_without_extraction(
    tmp_path: Path, member: str
) -> None:
    snapshot = make_snapshot(tmp_path, zipped=True, unsafe_member=member)
    with pytest.raises(DomainError, match="unsafe member"):
        tuple(CnpjBulkSource().records(snapshot, frozenset({"00000001"})))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["files"]


def test_wrong_column_count_fails_even_for_an_unselected_root(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    (snapshot.path / "files" / "Socios0.csv").write_bytes(b'"99999999";"2"\n')
    with pytest.raises(DomainError, match="expected 11"):
        tuple(CnpjBulkSource().records(snapshot, frozenset({"00000001"})))


def test_latin_one_names_and_missing_cnae_remain_exact(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path)
    company_path = snapshot.path / "files" / "Empresas0.csv"
    company_path.write_bytes(
        company_path.read_bytes().replace(
            b"SUPERMERCADO TESTE", "MERCEARIA JOÃO".encode("latin-1")
        )
    )
    establishment_path = snapshot.path / "files" / "Estabelecimentos0.csv"
    establishment_path.write_bytes(
        establishment_path.read_bytes().replace(b"4711302", b"")
    )
    records = tuple(CnpjBulkSource().records(snapshot, frozenset({"00000001"})))
    assert isinstance(records[0], CnpjCompany) and "JOÃO" in records[0].razao_social
    assert (
        isinstance(records[1], CnpjEstablishment) and records[1].cnae_principal is None
    )

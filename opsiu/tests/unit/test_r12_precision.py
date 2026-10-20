"""R12 requires a usable company identity and an explicit participation role."""

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from opsiu.adapters.duckdb.check_repository import DuckDBCheckRepository
from opsiu.domain.errors import DomainError
from opsiu.domain.identifiers import is_valid_numeric_cnpj, valid_numeric_cnpj_sql
from opsiu.domain.policies import R12_OWNERSHIP_QUALIFICATIONS
from opsiu.domain.rules.r12_sobrenome_socio import R12SobrenomeSocio

pytestmark = pytest.mark.unit


def candidate() -> dict[str, object]:
    return {
        "id_documento": 100,
        "valor_liquido": Decimal("100.00"),
        "cnpj": "00000000000191",
        "cnpj_root": "00000000",
        "covered_root": "00000000",
        "snapshot_id": "snapshot",
        "source_sha256": "a" * 64,
        "nome_parlamentar": "Ana Quirino",
        "socios": [{"nome": "Beatriz Quirino", "qualificacao": "49"}],
    }


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("00000000000191", True),
        ("00000001000136", True),
        ("11111111000191", True),
        ("11111111000272", True),
        ("00000000000000", False),
        ("11111111111111", False),
        ("00000000000001", False),
        ("00000000000100", False),
        ("00000000000190", False),
        ("00000000000181", False),
        ("12345678901", False),
        ("00.000.000/0001-91", False),
        ("０００００００００００１９１", False),
        ("00000000A00191", False),
        (" 00000000000191", False),
        ("", False),
        (None, False),
        (191, False),
    ],
)
def test_numeric_identity_and_sql_predicate_agree(value: object, valid: bool) -> None:
    assert is_valid_numeric_cnpj(value) is valid
    # Expenses expose a VARCHAR column. Non-string input is rejected by the
    # domain helper rather than coerced into a different company identity.
    if isinstance(value, str) or value is None:
        with duckdb.connect() as connection:
            match = connection.execute(
                "SELECT coalesce(("
                + valid_numeric_cnpj_sql("cnpj")
                + "), false) FROM (SELECT ?::VARCHAR AS cnpj)",
                [value],
            ).fetchone()
        assert match == (valid,)


@pytest.mark.parametrize("cnpj", ["00000000000001", "00000000000000", "00000000000100"])
def test_invalid_supplier_never_claims_bank_ownership_or_demands_coverage(
    cnpj: str,
) -> None:
    row = candidate()
    row["cnpj"] = cnpj
    assert R12SobrenomeSocio().evaluate(row) is None
    row["covered_root"] = None
    row["snapshot_id"] = None
    assert R12SobrenomeSocio().evaluate(row) is None


def test_valid_supplier_still_requires_receita_coverage() -> None:
    row = candidate()
    row["covered_root"] = None
    with pytest.raises(DomainError, match="successfully loaded"):
        R12SobrenomeSocio().evaluate(row)


@pytest.mark.parametrize(
    "qualification",
    [
        "05",
        "08",
        "10",
        "16",
        "17",
        "34",
        "54",
        "63",
        "69",
        "70",
        "75",
        "99",
        "",
        None,
        49,
    ],
)
def test_management_founder_and_unknown_roles_do_not_establish_participation(
    qualification: object,
) -> None:
    row = candidate()
    row["socios"] = [{"nome": "Beatriz Quirino", "qualificacao": qualification}]
    assert R12SobrenomeSocio().evaluate(row) is None


@pytest.mark.parametrize("qualification", sorted(R12_OWNERSHIP_QUALIFICATIONS))
def test_explicit_ownership_qualifications_remain_eligible(
    qualification: str,
) -> None:
    row = candidate()
    row["socios"] = [{"nome": "Beatriz Quirino", "qualificacao": qualification}]
    result = R12SobrenomeSocio().evaluate(row)
    assert result is not None
    assert result.evidence["tokens_compartilhados"] == ["QUIRINO"]
    assert result.evidence["parentesco_comprovado"] is False


def test_only_ownership_matches_contribute_names_and_external_source_proof() -> None:
    row = candidate()
    row["nome_parlamentar"] = "Ana Henrique Vieira Quirino"
    owner = {
        "nome": "Beatriz Henrique Quirino",
        "qualificacao": "49",
        "source_file": "Socios.zip",
        "source_sha256": "b" * 64,
        "source_member": "Socios.csv",
        "source_record_number": 42,
    }
    row["socios"] = [
        {"nome": "Maria Vieira", "qualificacao": "10"},
        owner,
        {"nome": "Carla Henrique", "qualificacao": "49"},
    ]
    row["valor_liquido"] = Decimal("-5.00")
    result = R12SobrenomeSocio().evaluate(row)
    assert result is not None
    assert result.amount == Decimal("-5.00")
    assert result.evidence["tokens_compartilhados"] == ["QUIRINO"]
    assert result.evidence["socios"] == [
        {**owner, "tokens_compartilhados": ["QUIRINO"]}
    ]


def test_sql_excludes_placeholders_before_coverage_and_keeps_bank_with_valid_id() -> (
    None
):
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE despesas_current AS SELECT 1 AS id_documento, "
            "100.00::DECIMAL(18,2) AS valor_liquido, "
            "'hash' AS record_sha256, 1 AS source_file_id, "
            "'ANA QUIRINO' AS nome_parlamentar, "
            "'00000000000001' AS cnpj_cpf_digits, "
            "'12345678901' AS cpf, 'SP' AS sigla_uf "
            "UNION ALL SELECT 2, -5.00, 'adjustment', 1, 'ANA QUIRINO', "
            "'00000000000191', '12345678901', 'SP'; "
            "CREATE TABLE source_files AS SELECT 1 AS source_file_id, "
            "2026 AS source_year; "
            "CREATE TABLE cnpj_imports_current AS SELECT 'snapshot' AS snapshot_id, "
            "'hash' AS source_sha256; "
            "CREATE TABLE cnpj_import_roots_current AS SELECT '00000000' AS cnpj_root; "
            "CREATE TABLE cnpj_socios_current AS SELECT '00000000' AS cnpj_root, "
            "2 AS tipo_socio, 'BEATRIZ QUIRINO' AS nome_socio, '49' AS qualificacao, "
            "'Socios.csv' AS source_file, 'filehash' AS source_sha256, "
            "'Socios.csv' AS source_member, 1 AS source_record_number"
        )
        candidates = tuple(
            DuckDBCheckRepository(connection).candidates(R12SobrenomeSocio.sql)
        )
    assert [row["id_documento"] for row in candidates] == [2]
    result = R12SobrenomeSocio().evaluate(candidates[0])
    assert result is not None and result.amount == Decimal("-5.00")


def test_documented_ownership_policy_matches_executable_allowlist() -> None:
    document = (
        Path(__file__).parents[2] / ".llms/rules/R12-sobrenome-socio.md"
    ).read_text()
    block = document.split("<!-- ownership-qualifications:start -->", 1)[1].split(
        "<!-- ownership-qualifications:end -->", 1
    )[0]
    assert json.loads(block.split("```json", 1)[1].split("```", 1)[0]) == sorted(
        R12_OWNERSHIP_QUALIFICATIONS
    )

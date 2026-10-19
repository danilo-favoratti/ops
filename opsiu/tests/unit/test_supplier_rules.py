"""Conservative surname and registered-activity review indicators."""

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from opsiu.adapters.duckdb.check_repository import DuckDBCheckRepository
from opsiu.domain.errors import DomainError
from opsiu.domain.policies import R12_COMMON_SURNAMES
from opsiu.domain.rules.r12_sobrenome_socio import R12SobrenomeSocio
from opsiu.domain.rules.r13_fornecedor_incompativel import R13FornecedorIncompativel
from opsiu.domain.text import surname_tokens

pytestmark = pytest.mark.unit


def row() -> dict[str, object]:
    return {
        "id_documento": 1,
        "valor_liquido": Decimal("12.34"),
        "cnpj": "00000001000100",
        "cnpj_root": "00000001",
        "covered_root": "00000001",
        "snapshot_id": "source",
        "source_sha256": "0" * 64,
        "nome_parlamentar": "JOÃO QUIRINO",
        "socios": [{"nome": "MARIA QUIRINO", "qualificacao": "49"}],
        "numero_sub_cota": 13,
        "razao_social": "COMERCIO TESTE",
        "natureza_juridica": "2062",
        "cnae_principal": None,
        "fornecedor": "SUPERMERCADO NA DESPESA",
        "company_origin": {},
        "establishment_origin": None,
    }


def test_rare_surname_matches_but_first_names_common_names_and_suffixes_do_not() -> (
    None
):
    assert surname_tokens("Quirino Silva Filho") == frozenset()
    assert surname_tokens("João Quirino Júnior") == frozenset({"QUIRINO"})
    result = R12SobrenomeSocio().evaluate(row())
    assert result is not None and result.amount == Decimal("12.34")
    assert result.evidence["tokens_compartilhados"] == ["QUIRINO"]
    assert result.evidence["parentesco_comprovado"] is False
    for name in ("QUIRINO SILVA", "MARIA SILVA", "ANA COSTA FILHO"):
        candidate = row()
        candidate["nome_parlamentar"] = name
        assert R12SobrenomeSocio().evaluate(candidate) is None


@pytest.mark.parametrize(
    ("category", "cnae", "name", "expected"),
    [
        (13, "4711301", "COMERCIO", True),
        (13, "4711302", "COMERCIO", True),
        (13, "4712100", "COMERCIO", True),
        (13, "5611201", "RESTAURANTE", False),
        (13, None, "HIPERMERCADOS TESTE", True),
        (13, None, "MINIMERCADO TESTE", True),
        (13, None, "MERCEARIA TESTE", True),
        (13, None, "MERCADO CENTRAL", False),
        (13, None, "SUPERMERCADOX", False),
        (120, "4923002", "LOCADORA", True),
        (120, None, "LOCACAO COM MOTORISTA", True),
        (120, None, "SERVIÇOS DE MOTORISTA LTDA", True),
        (120, None, "LOCACAO SEM MOTORISTA", False),
        (120, None, "SUPERMERCADO TESTE", False),
        (1, "4711302", "SUPERMERCADO TESTE", False),
    ],
)
def test_r13_exact_activity_and_legal_name_policies(
    category: int, cnae: str | None, name: str, expected: bool
) -> None:
    candidate = row()
    candidate.update(numero_sub_cota=category, cnae_principal=cnae, razao_social=name)
    result = R13FornecedorIncompativel().evaluate(candidate)
    assert (result is not None) == expected
    if result is not None:
        assert result.evidence["conteudo_da_nota_comprovado"] is False


@pytest.mark.parametrize("rule", [R12SobrenomeSocio(), R13FornecedorIncompativel()])
def test_missing_root_coverage_is_a_failure_not_negative_evidence(
    rule: R12SobrenomeSocio | R13FornecedorIncompativel,
) -> None:
    candidate = row()
    candidate["covered_root"] = None
    with pytest.raises(DomainError, match="successfully loaded"):
        rule.evaluate(candidate)


def test_documented_stoplist_matches_executable_policy() -> None:
    document = (
        Path(__file__).parents[2] / ".llms" / "rules" / "R12-sobrenome-socio.md"
    ).read_text()
    block = document.split("<!-- surname-stoplist:start -->", 1)[1].split(
        "<!-- surname-stoplist:end -->", 1
    )[0]
    values = json.loads(block.split("```json", 1)[1].split("```", 1)[0])
    assert values == sorted(R12_COMMON_SURNAMES)


def test_r12_excludes_institutional_leadership_but_keeps_real_deputy_without_cpf() -> (
    None
):
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE despesas_current AS SELECT 1 AS id_documento, "
            "12.34::DECIMAL(18,2) AS valor_liquido, "
            "'hash' AS record_sha256, 1 AS source_file_id, "
            "'LIDERANÇA DA CÂMARA' AS nome_parlamentar, "
            "'00000001000100' AS cnpj_cpf_digits, "
            "NULL::VARCHAR AS cpf, 'NA' AS sigla_uf "
            "UNION ALL SELECT 2, 12.34, 'hash', 1, 'ANA CAMARA', "
            "'00000001000100', NULL, 'DF'; "
            "CREATE TABLE source_files AS SELECT 1 AS source_file_id, "
            "2026 AS source_year; "
            "CREATE TABLE cnpj_imports_current AS SELECT 'snapshot' AS snapshot_id, "
            "'hash' AS source_sha256; "
            "CREATE TABLE cnpj_import_roots_current AS SELECT '00000001' AS cnpj_root; "
            "CREATE TABLE cnpj_socios_current AS SELECT '00000001' AS cnpj_root, "
            "2 AS tipo_socio, 'JOSE CAMARA' AS nome_socio, '49' AS qualificacao, "
            "'Socios.csv' AS source_file, 'filehash' AS source_sha256, "
            "'Socios.csv' AS source_member, 1 AS source_record_number"
        )
        candidates = tuple(
            DuckDBCheckRepository(connection).candidates(R12SobrenomeSocio.sql)
        )
    assert len(candidates) == 1
    result = R12SobrenomeSocio().evaluate(candidates[0])
    assert result is not None and result.id_documento == 2


@pytest.mark.parametrize(
    "name",
    [
        "Carlos",
        "Paula",
        "Luiz",
        "Augusto",
        "Antônio",
        "Henrique",
        "César",
        "Cristina",
        "Lucas",
        "Victor",
        "Fabiano",
        "Paulo",
        "José",
        "Eduardo",
        "Roberto",
        "Tadeu",
    ],
)
def test_r12_reviewer_excluded_given_names_do_not_trigger(name: str) -> None:
    candidate = row()
    candidate["nome_parlamentar"] = f"Ana {name}"
    candidate["socios"] = [{"nome": f"Beatriz {name.upper()}", "qualificacao": "49"}]
    assert R12SobrenomeSocio().evaluate(candidate) is None
    assert surname_tokens(f"Dr. {name}") == frozenset()
    assert surname_tokens(f"Dra. {name.upper()}") == frozenset()


def test_r12_keeps_independent_surname_and_only_eligible_partner_evidence() -> None:
    candidate = row()
    candidate["nome_parlamentar"] = "Ana Henrique Vieira"
    candidate["socios"] = [
        {"nome": "Beatriz Henrique Vieira", "qualificacao": "49"},
        {"nome": "Beatriz Henrique", "qualificacao": "49"},
    ]
    result = R12SobrenomeSocio().evaluate(candidate)
    assert result is not None
    assert result.evidence["tokens_compartilhados"] == ["VIEIRA"]
    assert result.evidence["socios"] == [
        {
            "nome": "Beatriz Henrique Vieira",
            "qualificacao": "49",
            "tokens_compartilhados": ["VIEIRA"],
        }
    ]
    assert result.evidence["parentesco_comprovado"] is False
    assert result.amount == Decimal("12.34")
    assert surname_tokens("Ana Carlota Luiza") == frozenset({"CARLOTA", "LUIZA"})
    assert R12SobrenomeSocio.descriptor.version == "3"


def test_documented_given_name_exclusions_match_reviewed_policy() -> None:
    from opsiu.domain.policies import R12_EXCLUDED_GIVEN_NAMES

    expected = frozenset(
        "CARLOS PAULA LUIZ AUGUSTO ANTONIO HENRIQUE CESAR CRISTINA LUCAS "
        "VICTOR FABIANO PAULO JOSE EDUARDO ROBERTO TADEU".split()
    )
    assert R12_EXCLUDED_GIVEN_NAMES == expected
    document = (
        Path(__file__).parents[2] / ".llms" / "rules" / "R12-sobrenome-socio.md"
    ).read_text()
    block = document.split("<!-- given-name-stoplist:start -->", 1)[1].split(
        "<!-- given-name-stoplist:end -->", 1
    )[0]
    assert json.loads(block.split("```json", 1)[1].split("```", 1)[0]) == sorted(
        expected
    )

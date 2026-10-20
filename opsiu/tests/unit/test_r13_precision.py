"""R13 requires a valid establishment identity and treats CNAE as a review lead."""

from decimal import Decimal

import pytest

from opsiu.domain.rules.r13_fornecedor_incompativel import R13FornecedorIncompativel

pytestmark = pytest.mark.unit


def candidate() -> dict[str, object]:
    return {
        "id_documento": 1,
        "valor_liquido": Decimal("123.45"),
        "cnpj": "00000001000136",
        "cnpj_root": "00000001",
        "covered_root": "00000001",
        "snapshot_id": "snapshot",
        "source_sha256": "a" * 64,
        "numero_sub_cota": 13,
        "razao_social": "SUPERMERCADO EXEMPLO",
        "natureza_juridica": "2062",
        "cnae_principal": "4711301",
        "fornecedor": "ALIMENTACAO NA DESPESA",
        "company_origin": {"source_member": "Empresas0.csv", "source_record_number": 5},
        "establishment_origin": {
            "source_member": "Estabelecimentos0.csv",
            "source_record_number": 8,
        },
    }


@pytest.mark.parametrize(
    "cnpj", ["00000000000001", "00000000000000", "00000001000100", "00000001"]
)
def test_placeholder_or_invalid_supplier_cannot_join_real_registry(cnpj: str) -> None:
    row = candidate()
    row["cnpj"] = cnpj
    # Invalid identifiers must not require source coverage or borrow another identity.
    row["snapshot_id"] = row["covered_root"] = None
    assert R13FornecedorIncompativel().evaluate(row) is None


@pytest.mark.parametrize(
    ("category", "name", "cnae"),
    [
        (13, "SUPERMERCADO EXEMPLO", "5611201"),
        (13, "HIPERMERCADO EXEMPLO", None),
        (13, "MERCEARIA EXEMPLO", "5611204"),
        (120, "LOCACAO COM MOTORISTA EXEMPLO", "7711000"),
        (120, "SERVICOS DE MOTORISTA EXEMPLO", None),
    ],
)
def test_company_name_does_not_substitute_transaction_evidence(
    category: int, name: str, cnae: str | None
) -> None:
    row = candidate()
    row.update(numero_sub_cota=category, razao_social=name, cnae_principal=cnae)
    assert R13FornecedorIncompativel().evaluate(row) is None


def test_driver_cnae_remains_an_explicit_review_lead_with_full_provenance() -> None:
    row = candidate()
    row.update(
        numero_sub_cota=120,
        razao_social="LOCADORA SEM MOTORISTA EXEMPLO",
        cnae_principal="4923002",
    )
    result = R13FornecedorIncompativel().evaluate(row)
    assert result is not None
    assert result.amount == Decimal("123.45")
    assert result.evidence["motivos"] == ["cnae_locacao_com_motorista"]
    assert result.evidence["indicio_apenas_cadastral"] is True
    assert result.evidence["conteudo_da_nota_comprovado"] is False
    assert result.evidence["cadastro_na_data_despesa"] is False
    assert result.evidence["revisao_humana_obrigatoria"] is True
    assert result.evidence["origem_empresa"] == row["company_origin"]
    assert result.evidence["origem_estabelecimento"] == row["establishment_origin"]
    assert result.evidence["snapshot_id"] == row["snapshot_id"]
    assert result.evidence["snapshot_sha256"] == row["source_sha256"]
    assert result.evidence["razao_social"] == row["razao_social"]
    assert R13FornecedorIncompativel.descriptor.severity == "revisar"
    assert R13FornecedorIncompativel.descriptor.version == "3"


def test_missing_legal_name_does_not_hide_exact_establishment_activity() -> None:
    row = candidate()
    row["razao_social"] = row["company_origin"] = None
    result = R13FornecedorIncompativel().evaluate(row)
    assert result is not None
    assert result.evidence["motivos"] == ["cnae_comercio_alimenticio"]
    assert result.evidence["origem_empresa"] is None

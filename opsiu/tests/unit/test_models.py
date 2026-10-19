"""Synthetic deputy and leadership records cover all public source fields."""

import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from opsiu.domain.errors import DomainError
from opsiu.domain.models import Competencia, Deputado, Despesa, PayerKind
from opsiu.domain.record_hash import record_sha256

pytestmark = pytest.mark.unit


@pytest.fixture
def deputy_raw() -> dict[str, object]:
    return {
        "nomeParlamentar": "Deputada Sintética",
        "cpf": "012.345.678-90",
        "numeroCarteiraParlamentar": 12,
        "legislatura": "2023",
        "siglaUF": "SP",
        "siglaPartido": "TESTE",
        "codigoLegislatura": 57,
        "numeroSubCota": "3",
        "descricao": "COMBUSTÍVEIS E LUBRIFICANTES.",
        "numeroEspecificacaoSubCota": "1",
        "descricaoEspecificacao": "Veículos",
        "fornecedor": "Fornecedor Sintético",
        "cnpjCPF": "01.234.567/0001-89",
        "numero": "000123",
        "tipoDocumento": "4",
        "dataEmissao": "2026-01-08T00:00:00",
        "valorDocumento": "1467",
        "valorGlosa": "12.50",
        "valorLiquido": "1454.50",
        "mes": "1",
        "ano": 2026,
        "parcela": "0",
        "passageiro": "Passageira Sintética",
        "trecho": "BSB/SP",
        "lote": "1234",
        "ressarcimento": "0",
        "datPagamentoRestituicao": "2026-02-01T12:00:00",
        "restituicao": "12,50",
        "numeroDeputadoID": 101,
        "idDocumento": "900001",
        "urlDocumento": "https://example.invalid/900001.pdf",
    }


@pytest.fixture
def leadership_raw(deputy_raw: dict[str, object]) -> dict[str, object]:
    return {
        **deputy_raw,
        "nomeParlamentar": "LIDERANÇA SINTÉTICA",
        "cpf": "",
        "siglaUF": "NA",
        "siglaPartido": "",
        "numeroCarteiraParlamentar": "",
        "numeroEspecificacaoSubCota": "",
        "descricaoEspecificacao": "",
        "passageiro": "",
        "trecho": "",
        "ressarcimento": "",
        "datPagamentoRestituicao": "",
        "restituicao": "",
        "numeroDeputadoID": 102,
        "idDocumento": 900002,
    }


def test_deputy_record_normalizes_every_source_field(
    deputy_raw: dict[str, object],
) -> None:
    original = deepcopy(deputy_raw)
    expense = Despesa.from_raw(deputy_raw)

    assert expense.nome_parlamentar == "Deputada Sintética"
    assert expense.cpf == "01234567890"
    assert expense.numero_carteira_parlamentar == 12
    assert expense.legislatura == 2023
    assert expense.sigla_uf == "SP"
    assert expense.sigla_partido == "TESTE"
    assert expense.codigo_legislatura == 57
    assert expense.numero_sub_cota == 3
    assert expense.descricao == "COMBUSTÍVEIS E LUBRIFICANTES."
    assert expense.numero_especificacao_sub_cota == 1
    assert expense.descricao_especificacao == "Veículos"
    assert expense.fornecedor == "Fornecedor Sintético"
    assert expense.cnpj_cpf == expense.cnpj_cpf_digits == "01234567000189"
    assert expense.payer_kind is PayerKind.CNPJ
    assert expense.numero == "000123"
    assert expense.tipo_documento == 4
    assert expense.data_emissao == date(2026, 1, 8)
    assert expense.valor_documento == Decimal("1467.00")
    assert expense.valor_glosa == Decimal("12.50")
    assert expense.valor_liquido == Decimal("1454.50")
    assert expense.competencia == Competencia(2026, 1)
    assert (expense.ano, expense.mes) == (2026, 1)
    assert expense.parcela == 0
    assert expense.passageiro == "Passageira Sintética"
    assert expense.trecho == "BSB/SP"
    assert expense.lote == 1234
    assert expense.ressarcimento == 0
    assert expense.data_pagamento_restituicao == date(2026, 2, 1)
    assert expense.restituicao == Decimal("12.50")
    assert expense.numero_deputado_id == 101
    assert expense.id_documento == 900001
    assert expense.url_documento == "https://example.invalid/900001.pdf"
    assert expense.record_sha256 == record_sha256(original)
    assert deputy_raw == original
    deputy = Deputado.from_despesa(expense)
    assert deputy.nome == expense.nome_parlamentar
    assert deputy.cpf == expense.cpf
    assert not deputy.lideranca
    with pytest.raises(FrozenInstanceError):
        setattr(expense, "id_documento", 2)


def test_leadership_and_empty_optional_values(
    leadership_raw: dict[str, object],
) -> None:
    expense = Despesa.from_raw(leadership_raw)
    deputy = Deputado.from_despesa(expense)

    assert deputy.lideranca
    assert deputy.uf == "NA"
    assert expense.cpf is None
    assert expense.sigla_partido is None
    assert expense.numero_carteira_parlamentar is None
    assert expense.numero_especificacao_sub_cota is None
    assert expense.descricao_especificacao is None
    assert expense.passageiro is None
    assert expense.trecho is None
    assert expense.ressarcimento is None
    assert expense.data_pagamento_restituicao is None
    assert expense.restituicao is None


@pytest.mark.parametrize(("cpf", "uf"), [("", "SP"), ("01234567890", "NA")])
def test_leadership_requires_both_source_conditions(
    deputy_raw: dict[str, object], cpf: str, uf: str
) -> None:
    deputy_raw.update(cpf=cpf, siglaUF=uf)

    assert not Deputado.from_despesa(Despesa.from_raw(deputy_raw)).lideranca


@pytest.mark.parametrize(
    ("payee", "digits", "kind"),
    [
        ("012.345.678-90", "01234567890", PayerKind.CPF),
        ("123", "123", PayerKind.OUTRO),
        ("  ", None, PayerKind.OUTRO),
    ],
)
def test_payee_kind_comes_from_normalized_length(
    deputy_raw: dict[str, object], payee: str, digits: str | None, kind: PayerKind
) -> None:
    deputy_raw["cnpjCPF"] = payee
    expense = Despesa.from_raw(deputy_raw)

    assert expense.cnpj_cpf == digits
    assert expense.payer_kind is kind


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("valorDocumento", "invalid"),
        ("valorLiquido", "NaN"),
        ("dataEmissao", "2026-02-30"),
        ("datPagamentoRestituicao", "31/12/2026"),
        ("mes", "13"),
        ("ano", 0),
        ("ano", True),
        ("idDocumento", 1.5),
        ("tipoDocumento", "1.0"),
    ],
)
def test_malformed_source_values_raise_domain_error(
    deputy_raw: dict[str, object], field: str, invalid: object
) -> None:
    deputy_raw[field] = invalid

    with pytest.raises(DomainError):
        Despesa.from_raw(deputy_raw)


def test_source_identifiers_preserve_zero_and_negative_sentinels(
    deputy_raw: dict[str, object],
) -> None:
    deputy_raw.update(idDocumento=0, numeroDeputadoID=-1, numeroSubCota=0)
    expense = Despesa.from_raw(deputy_raw)

    assert (
        expense.id_documento,
        expense.numero_deputado_id,
        expense.numero_sub_cota,
    ) == (0, -1, 0)


def test_raw_hash_is_canonical_and_precedes_normalization(
    deputy_raw: dict[str, object],
) -> None:
    reordered = dict(reversed(list(deputy_raw.items())))
    expected = json.dumps(
        deputy_raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

    assert record_sha256(deputy_raw) == record_sha256(reordered)
    assert (
        record_sha256(deputy_raw)
        == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    )
    changed = {**deputy_raw, "dataEmissao": "2026-01-08"}
    assert (
        Despesa.from_raw(changed).data_emissao
        == Despesa.from_raw(deputy_raw).data_emissao
    )
    assert record_sha256(changed) != record_sha256(deputy_raw)


@pytest.mark.parametrize("missing", [None, "", "  ", "absent"])
def test_missing_glosa_is_unknown_and_preserves_negative_net_and_hash(
    deputy_raw: dict[str, object], missing: object
) -> None:
    deputy_raw.update(valorDocumento="1247", valorLiquido="-1247", valorGlosa=missing)
    if missing == "absent":
        del deputy_raw["valorGlosa"]
    original = deepcopy(deputy_raw)
    expense = Despesa.from_raw(deputy_raw)
    assert expense.valor_glosa is None
    assert expense.valor_documento == Decimal("1247.00")
    assert expense.valor_liquido == Decimal("-1247.00")
    assert expense.record_sha256 == record_sha256(original)
    assert deputy_raw == original
    zero = Despesa.from_raw({**original, "valorGlosa": "0.00"})
    assert zero.valor_glosa == Decimal("0.00")
    assert expense.record_sha256 != zero.record_sha256


@pytest.mark.parametrize(
    "field,value",
    [
        ("valorDocumento", None),
        ("valorLiquido", None),
        ("valorGlosa", "invalid"),
        ("valorGlosa", False),
        ("valorGlosa", 1.25),
        ("valorGlosa", "NaN"),
    ],
)
def test_nullable_deduction_does_not_relax_other_money_validation(
    deputy_raw: dict[str, object], field: str, value: object
) -> None:
    with pytest.raises(DomainError):
        Despesa.from_raw({**deputy_raw, field: value})


@pytest.mark.parametrize("invalid", [float("nan"), Decimal("1.00"), date(2026, 1, 1)])
def test_hash_rejects_values_that_are_not_standard_json(invalid: object) -> None:
    with pytest.raises(DomainError):
        record_sha256({"invalid": invalid})

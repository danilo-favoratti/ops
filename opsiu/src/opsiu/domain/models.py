"""Typed, immutable CEAP records and parliamentary identities."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from opsiu.domain.errors import DomainError
from opsiu.domain.money import parse_money
from opsiu.domain.record_hash import record_sha256


class PayerKind(StrEnum):
    CNPJ = "CNPJ"
    CPF = "CPF"
    OUTRO = "OUTRO"


@dataclass(frozen=True, slots=True)
class Competencia:
    ano: int
    mes: int

    def __post_init__(self) -> None:
        if type(self.ano) is not int or type(self.mes) is not int:
            raise DomainError("competencia year and month must be integers")
        if not 1 <= self.ano <= 9999 or not 1 <= self.mes <= 12:
            raise DomainError("competencia requires a valid calendar year and month")


def _optional_text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainError(f"{key} must be a string")
    return value.strip() or None


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = _optional_text(raw, key)
    if value is None:
        raise DomainError(f"{key} is required")
    return value


def _optional_integer(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        return int(value)
    raise DomainError(f"{key} must be an integer")


def _required_integer(raw: Mapping[str, object], key: str) -> int:
    value = _optional_integer(raw, key)
    if value is None:
        raise DomainError(f"{key} is required")
    return value


def _optional_date(raw: Mapping[str, object], key: str) -> date | None:
    value = _optional_text(raw, key)
    if value is None:
        return None
    try:
        if "T" in value or " " in value:
            return datetime.fromisoformat(value).date()
        return date.fromisoformat(value)
    except ValueError as error:
        raise DomainError(f"{key} must be an ISO date or datetime") from error


def _optional_money(raw: Mapping[str, object], key: str) -> Decimal | None:
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return parse_money(value)


def _digits(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"[^0-9]", "", value) or None


@dataclass(frozen=True, slots=True)
class Despesa:
    nome_parlamentar: str
    cpf: str | None
    numero_carteira_parlamentar: int | None
    legislatura: int | None
    sigla_uf: str
    sigla_partido: str | None
    codigo_legislatura: int | None
    numero_sub_cota: int
    descricao: str | None
    numero_especificacao_sub_cota: int | None
    descricao_especificacao: str | None
    fornecedor: str | None
    cnpj_cpf: str | None
    payer_kind: PayerKind
    numero: str | None
    tipo_documento: int | None
    data_emissao: date | None
    valor_documento: Decimal
    valor_glosa: Decimal
    valor_liquido: Decimal
    competencia: Competencia
    parcela: int | None
    passageiro: str | None
    trecho: str | None
    lote: int | None
    ressarcimento: int | None
    data_pagamento_restituicao: date | None
    restituicao: Decimal | None
    numero_deputado_id: int
    id_documento: int
    url_documento: str | None
    record_sha256: str

    @property
    def ano(self) -> int:
        return self.competencia.ano

    @property
    def mes(self) -> int:
        return self.competencia.mes

    @property
    def cnpj_cpf_digits(self) -> str | None:
        return self.cnpj_cpf

    @classmethod
    def from_raw(cls, raw: Mapping[str, object]) -> Despesa:
        """Normalize a source record without changing its original content/hash."""
        payee = _digits(_optional_text(raw, "cnpjCPF"))
        payer_kind = {14: PayerKind.CNPJ, 11: PayerKind.CPF}.get(
            len(payee or ""), PayerKind.OUTRO
        )
        return cls(
            nome_parlamentar=_required_text(raw, "nomeParlamentar"),
            cpf=_digits(_optional_text(raw, "cpf")),
            numero_carteira_parlamentar=_optional_integer(
                raw, "numeroCarteiraParlamentar"
            ),
            legislatura=_optional_integer(raw, "legislatura"),
            sigla_uf=_required_text(raw, "siglaUF"),
            sigla_partido=_optional_text(raw, "siglaPartido"),
            codigo_legislatura=_optional_integer(raw, "codigoLegislatura"),
            numero_sub_cota=_required_integer(raw, "numeroSubCota"),
            descricao=_optional_text(raw, "descricao"),
            numero_especificacao_sub_cota=_optional_integer(
                raw, "numeroEspecificacaoSubCota"
            ),
            descricao_especificacao=_optional_text(raw, "descricaoEspecificacao"),
            fornecedor=_optional_text(raw, "fornecedor"),
            cnpj_cpf=payee,
            payer_kind=payer_kind,
            numero=_optional_text(raw, "numero"),
            tipo_documento=_optional_integer(raw, "tipoDocumento"),
            data_emissao=_optional_date(raw, "dataEmissao"),
            valor_documento=parse_money(raw.get("valorDocumento")),
            valor_glosa=parse_money(raw.get("valorGlosa")),
            valor_liquido=parse_money(raw.get("valorLiquido")),
            competencia=Competencia(
                _required_integer(raw, "ano"), _required_integer(raw, "mes")
            ),
            parcela=_optional_integer(raw, "parcela"),
            passageiro=_optional_text(raw, "passageiro"),
            trecho=_optional_text(raw, "trecho"),
            lote=_optional_integer(raw, "lote"),
            ressarcimento=_optional_integer(raw, "ressarcimento"),
            data_pagamento_restituicao=_optional_date(raw, "datPagamentoRestituicao"),
            restituicao=_optional_money(raw, "restituicao"),
            numero_deputado_id=_required_integer(raw, "numeroDeputadoID"),
            id_documento=_required_integer(raw, "idDocumento"),
            url_documento=_optional_text(raw, "urlDocumento"),
            record_sha256=record_sha256(raw),
        )


@dataclass(frozen=True, slots=True)
class Deputado:
    numero_deputado_id: int
    nome: str
    cpf: str | None
    uf: str
    partido: str | None
    legislatura: int | None
    codigo_legislatura: int | None
    numero_carteira_parlamentar: int | None
    lideranca: bool = field(init=False)

    def __post_init__(self) -> None:
        normalized_cpf = _digits(self.cpf)
        object.__setattr__(self, "cpf", normalized_cpf)
        object.__setattr__(
            self, "lideranca", normalized_cpf is None and self.uf == "NA"
        )

    @classmethod
    def from_despesa(cls, expense: Despesa) -> Deputado:
        return cls(
            expense.numero_deputado_id,
            expense.nome_parlamentar,
            expense.cpf,
            expense.sigla_uf,
            expense.sigla_partido,
            expense.legislatura,
            expense.codigo_legislatura,
            expense.numero_carteira_parlamentar,
        )

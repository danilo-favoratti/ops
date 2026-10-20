"""Strict scalar, nested-object, and immutable AgentRig codec boundaries."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import cast

import pytest
from agentrig.agents import AgentExecutionRequest, AgentExecutionResult
from agentrig.core import EffectProfile, JsonValue, RunContext

from opsiu.adapters.agents.contracts import (
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    NotaFiscalLeituraCodec,
    NotaFiscalRequestCodec,
    ParecerCodec,
    ParecerRequest,
    ParecerRequestCodec,
    ResumoDeputadoCodec,
    ResumoDeputadoRequestCodec,
    configure_nota_fiscal_agent,
    configure_parecer_agent,
    configure_resumo_deputado_agent,
)
from opsiu.adapters.agents.prompts import load_prompt

pytestmark = pytest.mark.unit


def expense() -> dict[str, JsonValue]:
    return {
        "id_documento": 42,
        "numero_deputado_id": 101,
        "nome_parlamentar": "Deputada de Teste",
        "sigla_uf": "DF",
        "sigla_partido": None,
        "ano": 2026,
        "mes": 2,
        "fornecedor": "Empresa de Teste",
        "cnpj_cpf": "12345678000190",
        "numero": "00042",
        "data_emissao": "2026-02-28",
        "valor_documento": "123456789012.34",
        "valor_glosa": "1.00",
        "valor_liquido": "123456789011.34",
        "url_documento": None,
    }


def finding() -> dict[str, JsonValue]:
    return {
        "finding_key": "a" * 64,
        "regra": "R01",
        "versao_regra": "1",
        "severidade": "alta",
        "valor_envolvido": "0.01",
        "evidencia_json": '{"total":"12713.01","limite":"12713.00"}',
        "source_sha256": "b" * 64,
        "record_sha256": "c" * 64,
    }


def opinion() -> dict[str, JsonValue]:
    return {
        "fundamento": "O código identificou excesso sobre o limite mensal.",
        "confianca": "alta",
        "evidencias_citadas": ["evidencia_json.total = 12713.01"],
        "recomendacao": "manter",
    }


def explanation_request() -> dict[str, JsonValue]:
    return {
        "achado": finding(),
        "registro": expense(),
        "texto_regra": "Limite de locação de veículos: R$ 12.713,00.",
        "texto_nota": None,
    }


def invoice() -> dict[str, JsonValue]:
    return {
        "emitente": "Empresa de Teste",
        "cnpj": "12345678000190",
        "data": "2024-02-29",
        "valor": "123456789012.34",
        "descricao": "Serviço informado na nota.",
        "divergencias": [],
        "limitacoes": ["O texto não prova a autenticidade da nota."],
    }


def summary() -> dict[str, JsonValue]:
    return {
        "resumo": "Um achado exige avaliação humana.",
        "ranking": [
            {
                "finding_key": "a" * 64,
                "severidade": "alta",
                "justificativa": "Excesso calculado pelo código.",
            }
        ],
        "pontos_atencao": ["Não existe aprovação humana registrada."],
    }


def test_all_contracts_round_trip_with_exact_money_and_frozen_nested_values() -> None:
    request_codec = ParecerRequestCodec()
    request = request_codec.decode(explanation_request())
    assert request_codec.encode(request) == explanation_request()
    assert request.registro.valor_documento == "123456789012.34"
    assert request.registro.numero == "00042"
    with pytest.raises(FrozenInstanceError):
        setattr(request.registro, "valor_documento", "0.00")
    output_codec = ParecerCodec()
    assert output_codec.encode(output_codec.decode(opinion())) == opinion()
    invoice_request: dict[str, JsonValue] = {
        "registro": expense(),
        "texto_pdf": "Texto original da nota",
    }
    invoice_request_codec = NotaFiscalRequestCodec()
    assert (
        invoice_request_codec.encode(invoice_request_codec.decode(invoice_request))
        == invoice_request
    )
    invoice_codec = NotaFiscalLeituraCodec()
    assert invoice_codec.encode(invoice_codec.decode(invoice())) == invoice()
    review_request: dict[str, JsonValue] = {
        "numero_deputado_id": 101,
        "nome_parlamentar": "Deputada de Teste",
        "achados": [{"achado": finding(), "parecer": opinion()}],
    }
    review_request_codec = ResumoDeputadoRequestCodec()
    assert (
        review_request_codec.encode(review_request_codec.decode(review_request))
        == review_request
    )
    summary_codec = ResumoDeputadoCodec()
    assert summary_codec.encode(summary_codec.decode(summary())) == summary()


@pytest.mark.parametrize(
    "value", [1, 0.01, True, "1", "1.2", "1.230", "1,00", "NaN", "1e2", " 1.00"]
)
def test_money_never_coerces_numbers_or_approximates_decimal_text(
    value: JsonValue,
) -> None:
    payload = invoice()
    payload["valor"] = value
    with pytest.raises(ValueError):
        NotaFiscalLeituraCodec().decode(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confianca", "certeza"),
        ("confianca", 0.99),
        ("recomendacao", "aprovado"),
        ("recomendacao", True),
        ("fundamento", "  \n"),
        ("evidencias_citadas", []),
        ("evidencias_citadas", ["  "]),
        ("evidencias_citadas", [123]),
    ],
)
def test_invalid_opinion_fields_are_rejected(field: str, value: JsonValue) -> None:
    payload = opinion()
    payload[field] = value
    with pytest.raises(ValueError):
        ParecerCodec().decode(payload)


@pytest.mark.parametrize("section", [None, "achado", "registro"])
def test_extra_keys_rejected_at_every_request_level(section: str | None) -> None:
    payload = explanation_request()
    target = (
        payload if section is None else cast(dict[str, JsonValue], payload[section])
    )
    target["decisao"] = "aprovado"
    with pytest.raises(ValueError):
        ParecerRequestCodec().decode(payload)


@pytest.mark.parametrize("section", [None, "ranking"])
def test_extra_keys_rejected_at_every_summary_level(section: str | None) -> None:
    payload = summary()
    if section is None:
        target = payload
    else:
        target = cast(list[dict[str, JsonValue]], payload["ranking"])[0]
    target["decisao"] = "aprovado"
    with pytest.raises(ValueError):
        ResumoDeputadoCodec().decode(payload)


@pytest.mark.parametrize(
    "value",
    [
        "[]",
        "null",
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e309}',
        '{"x":1,"x":2}',
        '{"nested":{"x":1,"x":2}}',
    ],
)
def test_evidence_must_be_an_unambiguous_finite_json_object(value: str) -> None:
    payload = explanation_request()
    cast(dict[str, JsonValue], payload["achado"])["evidencia_json"] = value
    with pytest.raises(ValueError):
        ParecerRequestCodec().decode(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mes", True),
        ("mes", "2"),
        ("mes", 13),
        ("id_documento", 42.0),
        ("data_emissao", "2026-02-29"),
        ("data_emissao", "20260228"),
    ],
)
def test_request_scalar_types_and_dates_are_strict(
    field: str, value: JsonValue
) -> None:
    payload = explanation_request()
    cast(dict[str, JsonValue], payload["registro"])[field] = value
    with pytest.raises(ValueError):
        ParecerRequestCodec().decode(payload)


def test_unknown_invoice_fields_stay_null_and_invalid_identifiers_fail() -> None:
    payload = invoice()
    for field in ("emitente", "cnpj", "data", "valor", "descricao"):
        payload[field] = None
    assert NotaFiscalLeituraCodec().decode(payload).valor is None
    payload["cnpj"] = "12.345.678/0001-90"
    with pytest.raises(ValueError):
        NotaFiscalLeituraCodec().decode(payload)


def test_agent_execution_frozen_output_is_decoded_without_mapping_coercion() -> None:
    execution = AgentExecutionResult.succeeded(summary())
    result = ResumoDeputadoCodec().decode(execution.result.unwrap())
    assert result.ranking[0].finding_key == "a" * 64


def test_encode_revalidates_even_instances_corrupted_after_construction() -> None:
    codec = ParecerRequestCodec()
    request = codec.decode(explanation_request())
    object.__setattr__(request.registro, "valor_documento", 12.34)
    with pytest.raises(ValueError):
        codec.encode(request)
    with pytest.raises(ValueError):
        codec.encode(cast(ParecerRequest, explanation_request()))


def test_missing_fields_and_duplicate_ranked_findings_are_rejected() -> None:
    payload = opinion()
    del payload["recomendacao"]
    with pytest.raises(ValueError):
        ParecerCodec().decode(payload)
    payload = summary()
    ranking = cast(list[JsonValue], payload["ranking"])
    ranking.append(ranking[0])
    with pytest.raises(ValueError, match="unique finding keys"):
        ResumoDeputadoCodec().decode(payload)


def test_schemas_close_every_nested_object_and_require_every_property() -> None:
    def check(value: JsonValue) -> None:
        if isinstance(value, Mapping):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                properties = cast(Mapping[str, JsonValue], value["properties"])
                assert set(cast(list[str], value["required"])) == set(properties)
            for child in value.values():
                check(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                check(child)

    assert len(INPUT_SCHEMAS) == len(OUTPUT_SCHEMAS) == 3
    for schema in (*INPUT_SCHEMAS.values(), *OUTPUT_SCHEMAS.values()):
        check(schema)


class UnusedRuntime:
    async def execute(
        self, request: AgentExecutionRequest, context: RunContext
    ) -> AgentExecutionResult:
        raise AssertionError("factories must not execute a runtime")


def test_factories_bind_only_injected_capabilities_and_read_only_authority() -> None:
    runtime = UnusedRuntime()
    capabilities = ("test.scripted",)
    agents = (
        configure_parecer_agent(runtime, capabilities, rule_id="R12"),
        configure_nota_fiscal_agent(runtime, capabilities),
        configure_resumo_deputado_agent(runtime, capabilities),
    )
    for agent in agents:
        contract = agent.contract
        assert agent.runtime is runtime
        assert contract.effect_profile is EffectProfile.READ_ONLY
        assert contract.allowed_capabilities == capabilities
        assert contract.allowed_tools == ()
        assert contract.permissions == {"workspace": "read_only", "network": "denied"}
        assert contract.limits.max_turns == 1
        assert contract.limits.max_tool_calls == 0
        assert contract.prompt_version == (
            "3" if contract.agent_id.startswith("explicar-achado-") else "2"
        )
        assert contract.input_schema in INPUT_SCHEMAS
        assert contract.output_schema in OUTPUT_SCHEMAS
        assert agent.instructions
    assert agents[0].contract.agent_id == "explicar-achado-R12"
    with pytest.raises(ValueError, match="known rule ID"):
        configure_parecer_agent(runtime, capabilities, rule_id="R99")
    with pytest.raises(ValueError, match="does not match"):
        configure_nota_fiscal_agent(
            runtime, capabilities, prompt=load_prompt("explicar-achado")
        )

"""Strict, provider-neutral annotation contracts; agents never change findings."""

import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Annotated, Generic, Literal, TypeVar, cast

from agentrig.agents import AgentContract, AgentLimits, AgentRuntime, ConfiguredAgent
from agentrig.core import EffectProfile, JsonValue
from pydantic import AfterValidator, ConfigDict, Field, StringConstraints, TypeAdapter
from pydantic.dataclasses import dataclass

from opsiu.adapters.agents.prompts import Prompt, load_prompt

_CONFIG = ConfigDict(strict=True, extra="forbid", revalidate_instances="always")
Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
MoneyText = Annotated[
    str, StringConstraints(pattern=r"^-?(?:0|[1-9][0-9]*)\.[0-9]{2}$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Cnpj = Annotated[str, StringConstraints(pattern=r"^[0-9]{14}$")]
Severity = Literal["alta", "media", "revisar"]
Confidence = Literal["alta", "media", "baixa"]
Recommendation = Literal["manter", "descartar", "revisar"]


def _date_text(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("dates must use canonical YYYY-MM-DD format")
    return value


def _evidence_text(value: str) -> str:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"nonfinite JSON value: {constant}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, child in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON evidence key: {key}")
            result[key] = child
        return result

    parsed: object = json.loads(
        value, parse_constant=reject_constant, object_pairs_hook=unique_object
    )
    if not isinstance(parsed, dict):
        raise ValueError("finding evidence must encode a JSON object")
    json.dumps(parsed, allow_nan=False)
    return value


DateText = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    AfterValidator(_date_text),
]
EvidenceText = Annotated[Text, AfterValidator(_evidence_text)]


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class RegistroDespesa:
    id_documento: int
    numero_deputado_id: int
    nome_parlamentar: Text
    sigla_uf: Text
    sigla_partido: Text | None
    ano: Annotated[int, Field(ge=1, le=9999)]
    mes: Annotated[int, Field(ge=1, le=12)]
    fornecedor: Text | None
    cnpj_cpf: Text | None
    numero: Text | None
    data_emissao: DateText | None
    valor_documento: MoneyText
    valor_glosa: MoneyText
    valor_liquido: MoneyText
    url_documento: Text | None


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class AchadoContext:
    finding_key: Sha256
    regra: Annotated[str, StringConstraints(pattern=r"^R(?:0[1-9]|1[0-4])$")]
    versao_regra: Text
    severidade: Severity
    valor_envolvido: MoneyText
    evidencia_json: EvidenceText
    source_sha256: Sha256
    record_sha256: Sha256


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class ParecerRequest:
    achado: AchadoContext
    registro: RegistroDespesa
    texto_regra: Text
    texto_nota: Text | None


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class Parecer:
    fundamento: Text
    confianca: Confidence
    evidencias_citadas: Annotated[tuple[Text, ...], Field(min_length=1)]
    recomendacao: Recommendation


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class NotaFiscalRequest:
    registro: RegistroDespesa
    texto_pdf: Text


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class NotaFiscalLeitura:
    emitente: Text | None
    cnpj: Cnpj | None
    data: DateText | None
    valor: MoneyText | None
    descricao: Text | None
    divergencias: tuple[Text, ...]
    limitacoes: tuple[Text, ...]


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class AchadoComParecer:
    achado: AchadoContext
    parecer: Parecer


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class ResumoDeputadoRequest:
    numero_deputado_id: int
    nome_parlamentar: Text
    achados: Annotated[tuple[AchadoComParecer, ...], Field(min_length=1)]


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class PrioridadeAchado:
    finding_key: Sha256
    severidade: Severity
    justificativa: Text


@dataclass(frozen=True, slots=True, kw_only=True, config=_CONFIG)
class ResumoDeputado:
    resumo: Text
    ranking: Annotated[tuple[PrioridadeAchado, ...], Field(min_length=1)]
    pontos_atencao: tuple[Text, ...]


ValueT = TypeVar("ValueT")


def _plain_json(value: JsonValue) -> JsonValue:
    """Copy AgentRig's immutable mapping/tuple outputs into JSON containers."""
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be strings")
        return {key: _plain_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(child) for child in value]
    if value is not None and type(value) not in {str, int, float, bool}:
        raise ValueError("only JSON scalar values are allowed")
    return value


class StrictCodec(Generic[ValueT]):
    """Validate actual dataclasses on encode and exact JSON values on decode.

    JSON decoding permits arrays for typed tuples, but does not coerce scalar
    types. Revalidation also rejects corrupted instances on the encode path.
    """

    def __init__(self, model: type[ValueT], schema_id: str) -> None:
        self.model = model
        self.schema_id = schema_id
        self.adapter: TypeAdapter[ValueT] = TypeAdapter(model)

    def encode(self, value: ValueT) -> JsonValue:
        if type(value) is not self.model:
            raise ValueError(f"{self.model.__name__} instance required")
        validated = self.adapter.validate_python(value, strict=True)
        self._validate_unique_keys(validated)
        return cast(JsonValue, self.adapter.dump_python(validated, mode="json"))

    def decode(self, value: JsonValue) -> ValueT:
        if not isinstance(value, Mapping):
            raise ValueError(f"{self.model.__name__} JSON object required")
        encoded = json.dumps(_plain_json(value), ensure_ascii=False, allow_nan=False)
        result = self.adapter.validate_json(encoded, strict=True)
        self._validate_unique_keys(result)
        return result

    def _validate_unique_keys(self, value: ValueT) -> None:
        if isinstance(value, ResumoDeputadoRequest):
            keys = [item.achado.finding_key for item in value.achados]
        elif isinstance(value, ResumoDeputado):
            keys = [item.finding_key for item in value.ranking]
        else:
            return
        if len(keys) != len(set(keys)):
            raise ValueError("deputy findings must have unique finding keys")

    def json_schema(self) -> Mapping[str, JsonValue]:
        return cast(Mapping[str, JsonValue], self.adapter.json_schema())


class ParecerRequestCodec(StrictCodec[ParecerRequest]):
    def __init__(self) -> None:
        super().__init__(ParecerRequest, "opsiu.parecer-request.v1")


class ParecerCodec(StrictCodec[Parecer]):
    def __init__(self) -> None:
        super().__init__(Parecer, "opsiu.parecer.v1")


class NotaFiscalRequestCodec(StrictCodec[NotaFiscalRequest]):
    def __init__(self) -> None:
        super().__init__(NotaFiscalRequest, "opsiu.nota-fiscal-request.v1")


class NotaFiscalLeituraCodec(StrictCodec[NotaFiscalLeitura]):
    def __init__(self) -> None:
        super().__init__(NotaFiscalLeitura, "opsiu.nota-fiscal-leitura.v1")


class ResumoDeputadoRequestCodec(StrictCodec[ResumoDeputadoRequest]):
    def __init__(self) -> None:
        super().__init__(ResumoDeputadoRequest, "opsiu.resumo-deputado-request.v1")


class ResumoDeputadoCodec(StrictCodec[ResumoDeputado]):
    def __init__(self) -> None:
        super().__init__(ResumoDeputado, "opsiu.resumo-deputado.v1")


INPUT_SCHEMAS = {
    codec.schema_id: codec.json_schema()
    for codec in (
        ParecerRequestCodec(),
        NotaFiscalRequestCodec(),
        ResumoDeputadoRequestCodec(),
    )
}
OUTPUT_SCHEMAS = {
    codec.schema_id: codec.json_schema()
    for codec in (ParecerCodec(), NotaFiscalLeituraCodec(), ResumoDeputadoCodec())
}

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


def _configure(
    runtime: AgentRuntime,
    allowed_capabilities: tuple[str, ...],
    *,
    agent_id: str,
    purpose: str,
    task: str,
    prompt: Prompt | None,
    input_codec: StrictCodec[InputT],
    output_codec: StrictCodec[OutputT],
) -> ConfiguredAgent[InputT, OutputT]:
    loaded = prompt if prompt is not None else load_prompt(task)
    if loaded.name != task:
        raise ValueError("agent prompt does not match its task")
    return ConfiguredAgent(
        runtime=runtime,
        contract=AgentContract(
            agent_id=agent_id,
            version="1",
            purpose=purpose,
            input_schema=input_codec.schema_id,
            output_schema=output_codec.schema_id,
            prompt_version=loaded.version,
            effect_profile=EffectProfile.READ_ONLY,
            limits=AgentLimits(max_turns=1, max_tool_calls=0),
            stopping_policy="output_schema_satisfied",
            allowed_capabilities=allowed_capabilities,
            permissions={"workspace": "read_only", "network": "denied"},
        ),
        instructions=loaded.text,
        input_codec=input_codec,
        output_codec=output_codec,
    )


def configure_parecer_agent(
    runtime: AgentRuntime,
    allowed_capabilities: tuple[str, ...],
    *,
    rule_id: str,
    prompt: Prompt | None = None,
) -> ConfiguredAgent[ParecerRequest, Parecer]:
    if re.fullmatch(r"R(?:0[1-9]|1[0-4])", rule_id) is None:
        raise ValueError("known rule ID required")
    return _configure(
        runtime,
        allowed_capabilities,
        agent_id=f"explicar-achado-{rule_id}",
        purpose="Explicar um achado determinístico sem decidir sua aprovação",
        task="explicar-achado",
        prompt=prompt,
        input_codec=ParecerRequestCodec(),
        output_codec=ParecerCodec(),
    )


def configure_nota_fiscal_agent(
    runtime: AgentRuntime,
    allowed_capabilities: tuple[str, ...],
    *,
    prompt: Prompt | None = None,
) -> ConfiguredAgent[NotaFiscalRequest, NotaFiscalLeitura]:
    return _configure(
        runtime,
        allowed_capabilities,
        agent_id="revisar-nota",
        purpose="Extrair campos da nota fornecida e anotar divergências",
        task="revisar-nota",
        prompt=prompt,
        input_codec=NotaFiscalRequestCodec(),
        output_codec=NotaFiscalLeituraCodec(),
    )


def configure_resumo_deputado_agent(
    runtime: AgentRuntime,
    allowed_capabilities: tuple[str, ...],
    *,
    prompt: Prompt | None = None,
) -> ConfiguredAgent[ResumoDeputadoRequest, ResumoDeputado]:
    return _configure(
        runtime,
        allowed_capabilities,
        agent_id="consolidar-deputado",
        purpose="Priorizar achados existentes para a revisão humana",
        task="consolidar-deputado",
        prompt=prompt,
        input_codec=ResumoDeputadoRequestCodec(),
        output_codec=ResumoDeputadoCodec(),
    )

"""Per-deputy summaries retain every active finding and its exact source opinion."""

import hashlib
import json

from opsiu.application.ports import (
    DeputyReviewer,
    DeputyReviewInput,
    JsonValue,
    SummaryRepository,
)
from opsiu.application.runs import ConfigurationError, Run
from opsiu.domain.findings import evidence_json


def summary_agent_input(request: DeputyReviewInput) -> dict[str, JsonValue]:
    """Portable input shared by the reviewer and its persisted-origin validator."""
    findings: list[JsonValue] = []
    for item in sorted(request.findings, key=lambda item: item.finding.finding_key):
        finding = item.finding
        findings.append(
            {
                "achado": {
                    "finding_key": finding.finding_key,
                    "regra": finding.rule_id,
                    "versao_regra": finding.rule_version,
                    "severidade": finding.severity,
                    "valor_envolvido": format(finding.amount, ".2f"),
                    "evidencia_json": evidence_json(finding.evidence),
                    "source_sha256": finding.provenance.source.source_sha256,
                    "record_sha256": finding.provenance.source.record_sha256,
                },
                "parecer": item.parecer,
            }
        )
    return {
        "numero_deputado_id": request.numero_deputado_id,
        "nome_parlamentar": request.nome_parlamentar,
        "achados": findings,
    }


def summary_input_json(request: DeputyReviewInput) -> str:
    sources: list[JsonValue] = []
    for item in sorted(request.findings, key=lambda item: item.finding.finding_key):
        finding = item.finding
        origin = finding.provenance
        sources.append(
            {
                "finding_key": finding.finding_key,
                "parecer_id": item.parecer_id,
                "parecer_trace_id": item.parecer_trace_id,
                "numero_deputado_id": item.numero_deputado_id,
                "nome_parlamentar": item.nome_parlamentar,
                "ano": item.ano,
                "mes": item.mes,
                "id_documento": finding.id_documento,
                "finding_run_id": origin.run_id,
                "finding_step_id": origin.step_id,
                "source_file_id": origin.source.source_file_id,
                "source_file": origin.source.source_file,
                "source_sha256": origin.source.source_sha256,
                "record_sha256": origin.source.record_sha256,
                "code_commit": origin.code_commit,
                "finding_created_at": origin.created_at.isoformat(),
            }
        )
    return json.dumps(
        {"agent_input": summary_agent_input(request), "sources": sources},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def summary_input_hash(request: DeputyReviewInput) -> str:
    return hashlib.sha256(summary_input_json(request).encode("utf-8")).hexdigest()


async def consolidate(
    run: Run,
    repository: SummaryRepository,
    reviewer: DeputyReviewer,
    *,
    max_calls: int,
    deputy_id: int | None = None,
    force: bool = False,
) -> None:
    if type(max_calls) is not int or max_calls < 0:
        raise ConfigurationError("O orçamento de chamadas não pode ser negativo.")
    for name in (
        "deputies_selected",
        "summary_calls",
        "summaries_saved",
        "summaries_skipped",
        "summaries_failed",
        "budget_exhausted",
    ):
        run.counters[name] = 0
    run.counters.setdefault("agent_calls", 0)
    spec = reviewer.spec()
    run.metadata["agent_budget"] = max_calls
    run.metadata["summary_source_model"] = spec.model
    inputs = repository.inputs(spec.model, deputy_id=deputy_id)
    try:
        with run.step("selecionar_deputados"):
            for request in inputs:
                run.counters["deputies_selected"] += 1
                with run.step(
                    f"consolidar_deputado_{request.numero_deputado_id}"
                ) as step:
                    digest = summary_input_hash(request)
                    exists = repository.has_success(
                        request.numero_deputado_id, digest, spec
                    )
                    if exists and not force:
                        run.counters["summaries_skipped"] += 1
                        step.skip("Já existe resumo para estes achados e pareceres.")
                        continue
                    if run.counters["summary_calls"] >= max_calls:
                        run.counters["budget_exhausted"] = 1
                        step.skip(
                            "O orçamento de chamadas desta execução foi esgotado."
                        )
                        run.emit(
                            "budget.exhausted",
                            {
                                "max_calls": max_calls,
                                "calls": run.counters["summary_calls"],
                                "numero_deputado_id": request.numero_deputado_id,
                            },
                        )
                        return
                    details: dict[str, JsonValue] = {
                        "numero_deputado_id": request.numero_deputado_id,
                        "input_sha256": digest,
                        "model": spec.model,
                        "step_id": run.require_step(),
                        "sources": [
                            {
                                "finding_key": item.finding.finding_key,
                                "parecer_id": item.parecer_id,
                                "parecer_trace_id": item.parecer_trace_id,
                            }
                            for item in request.findings
                        ],
                    }
                    contexts = run.metadata.setdefault("summary_inputs", [])
                    assert isinstance(contexts, list)
                    contexts.append(details)
                    run.emit("summary.context", details)
                    run.counters["summary_calls"] += 1
                    try:
                        result = await reviewer.summarize(request)
                    except Exception:
                        raise RuntimeError(
                            "A consolidação falhou; consulte os rastros privados."
                        ) from None
                    if result.failure is not None or result.output is None:
                        run.emit("summary.failed", {"agent_trace_id": result.trace_id})
                        raise RuntimeError(
                            f"Falha do revisor no rastro {result.trace_id}."
                        )
                    identity = run.ids.new_id()
                    repository.save(
                        request,
                        digest,
                        spec,
                        result,
                        summary_id=identity,
                        created_at=run.clock.now(),
                        run_id=run.run_id,
                        step_id=run.require_step(),
                    )
                    run.counters["summaries_saved"] += 1
                    run.emit(
                        "summary.saved",
                        {
                            "summary_id": identity,
                            "agent_trace_id": result.trace_id,
                            "numero_deputado_id": request.numero_deputado_id,
                            "input_sha256": digest,
                        },
                    )
                if step.record.status == "failed":
                    run.counters["summaries_failed"] += 1
    finally:
        close = getattr(inputs, "close", None)
        if callable(close):
            close()

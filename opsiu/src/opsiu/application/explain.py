"""Bounded annotation orchestration over immutable deterministic findings."""

from collections.abc import Mapping

from opsiu.application.ports import (
    AgentGateway,
    AnnotationKind,
    AnnotationRepository,
    AnnotationRequest,
    FindingExportReader,
    JsonValue,
    PdfRepository,
    RunReader,
)
from opsiu.application.runs import ConfigurationError, Run
from opsiu.application.sampling import sample_findings


class AnnotationFailure(RuntimeError):
    """Public failure deliberately excludes private request and response content."""


async def explain(
    run: Run,
    run_reader: RunReader,
    finding_reader: FindingExportReader,
    pdf_repository: PdfRepository,
    annotation_repository: AnnotationRepository,
    gateway: AgentGateway,
    *,
    rule_texts: Mapping[str, tuple[str, str]],
    selected_rules: tuple[str, ...],
    per_rule: int,
    max_calls: int,
    force: bool = False,
) -> None:
    if type(per_rule) is not int or per_rule < 1:
        raise ConfigurationError("A amostra por regra deve ser maior que zero.")
    if type(max_calls) is not int or max_calls < 0:
        raise ConfigurationError("O orçamento de chamadas não pode ser negativo.")
    for name in (
        "findings_sampled",
        "annotation_calls",
        "annotations_saved",
        "annotations_skipped",
        "annotations_failed",
        "budget_exhausted",
    ):
        run.counters[name] = 0
    run.counters.setdefault("agent_calls", 0)
    with run.step("selecionar_amostra") as selection:
        sample = sample_findings(run_reader, finding_reader, per_rule=per_rule)
        records = tuple(
            record
            for record in sample.records
            if record.finding.rule_id in selected_rules
        )
        description: dict[str, JsonValue] = {
            "check_run_id": sample.check_run_id,
            "per_rule": per_rule,
            "rules": list(selected_rules),
            "finding_keys": [record.finding.finding_key for record in records],
        }
        run.metadata["sample"] = description
        run.metadata["agent_budget"] = max_calls
        run.counters["findings_sampled"] = len(records)
        run.emit("findings.sampled", description)
    if selection.record.status == "failed":
        return

    for record in records:
        finding = record.finding
        with run.step(f"preparar_achado_{finding.id_documento}") as preparation:
            version, rule_text = rule_texts[finding.rule_id]
            if version != finding.rule_version:
                raise ValueError("O texto da regra não corresponde à versão do achado.")
            pdf = (
                pdf_repository.latest(finding.id_documento, record.url_documento)
                if record.url_documento is not None
                else None
            )
            request = AnnotationRequest(record, rule_text, pdf)
        if preparation.record.status == "failed":
            run.counters["annotations_failed"] += 1
            continue
        kinds: tuple[AnnotationKind, ...] = ("parecer", "nota_fiscal")
        for kind in kinds:
            with run.step(f"anotar_{kind}_{finding.id_documento}") as step:
                if kind == "nota_fiscal" and (
                    pdf is None or pdf.content.needs_ocr or not pdf.content.text.strip()
                ):
                    run.counters["annotations_skipped"] += 1
                    step.skip("Não há texto utilizável de nota fiscal armazenada.")
                    run.emit(
                        "annotation.skipped",
                        {
                            "finding_key": finding.finding_key,
                            "kind": kind,
                            "reason": "pdf_text_unavailable",
                        },
                    )
                    continue
                spec = gateway.spec(kind, finding.rule_id)
                existing = annotation_repository.has_success(finding.finding_key, spec)
                if existing and not force:
                    run.counters["annotations_skipped"] += 1
                    step.skip("Já existe anotação concluída para este agente e versão.")
                    run.emit(
                        "annotation.skipped",
                        {
                            "finding_key": finding.finding_key,
                            "kind": kind,
                            "reason": "already_annotated",
                        },
                    )
                    continue
                if run.counters["annotation_calls"] >= max_calls:
                    run.counters["budget_exhausted"] = 1
                    step.skip("O orçamento de chamadas desta execução foi esgotado.")
                    run.emit(
                        "budget.exhausted",
                        {
                            "max_calls": max_calls,
                            "calls": run.counters["annotation_calls"],
                            "finding_key": finding.finding_key,
                            "kind": kind,
                        },
                    )
                    return
                details: dict[str, JsonValue] = {
                    "finding_key": finding.finding_key,
                    "kind": kind,
                    "agent_id": spec.agent_id,
                    "model": spec.model,
                    "prompt_name": spec.prompt_name,
                    "prompt_version": spec.prompt_version,
                    "prompt_sha256": spec.prompt_sha256,
                    "source_file_id": finding.provenance.source.source_file_id,
                    "source_sha256": finding.provenance.source.source_sha256,
                    "record_sha256": finding.provenance.source.record_sha256,
                    "step_id": run.require_step(),
                    "pdf": (
                        {
                            "extraction_id": pdf.extraction_id,
                            "snapshot_id": pdf.content.snapshot_id,
                            "snapshot_sha256": pdf.content.snapshot_sha256,
                            "text_sha256": pdf.content.text_sha256,
                            "extractor": pdf.content.extractor,
                            "extractor_version": pdf.content.extractor_version,
                            "needs_ocr": pdf.content.needs_ocr,
                            "url": pdf.content.url,
                        }
                        if pdf is not None
                        else None
                    ),
                }
                inputs = run.metadata.setdefault("annotation_inputs", [])
                assert isinstance(inputs, list)
                inputs.append(details)
                run.emit("annotation.context", details)
                run.counters["annotation_calls"] += 1
                try:
                    result = await gateway.annotate(request, kind=kind)
                except Exception:
                    raise AnnotationFailure(
                        "A chamada do agente falhou; "
                        "consulte os rastros privados disponíveis."
                    ) from None
                if result.failure is not None or result.output is None:
                    run.emit(
                        "annotation.failed",
                        {
                            "finding_key": finding.finding_key,
                            "kind": kind,
                            "agent_trace_id": result.trace_id,
                        },
                    )
                    raise AnnotationFailure(
                        f"Falha do agente no rastro {result.trace_id}."
                    )
                annotation_repository.save(
                    finding.finding_key,
                    spec,
                    result,
                    parecer_id=run.ids.new_id(),
                    created_at=run.clock.now(),
                    run_id=run.run_id,
                    step_id=run.require_step(),
                )
                run.counters["annotations_saved"] += 1
                run.emit(
                    "annotation.saved",
                    {
                        "finding_key": finding.finding_key,
                        "kind": kind,
                        "agent_trace_id": result.trace_id,
                    },
                )
            if step.record.status == "failed":
                run.counters["annotations_failed"] += 1

"""Export human-approved findings without replacing their original evidence."""

import csv
import html
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from io import StringIO
from itertools import groupby

from opsiu.application.ports import (
    Artifact,
    ArtifactWriter,
    FinalReportReader,
    FinalReportRecord,
    FindingExportRecord,
    JsonValue,
)
from opsiu.application.report import CSV_FIELDS
from opsiu.application.runs import Run
from opsiu.domain.decisions import DecisionValue
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import evidence_json
from opsiu.domain.money import brl

FINAL_CSV_FIELDS = CSV_FIELDS + (
    "decision_id",
    "decision",
    "reviewer",
    "comment",
    "decided_at",
    "decision_run_id",
    "decision_step_id",
    "pareceres_json",
)
type RuleTotals = dict[tuple[str, str], tuple[int, Decimal]]

_LIMITATIONS = (
    "Os valores sinalizados, mesmo após aprovação humana, não representam "
    "prejuízo comprovado nem valores únicos recuperáveis. R07 e R14 incluem "
    "excedentes acumulados que se sobrepõem entre meses; R09 repete o valor "
    "bruto nos documentos de uma possível duplicidade. Regras diferentes "
    "também podem sinalizar a mesma despesa."
)


def _text(value: object) -> str:
    """Treat all source, reviewer and agent prose as inert Markdown text."""
    escaped = html.escape(
        str(value) if value is not None else "não informado", quote=False
    )
    escaped = " ".join(escaped.splitlines())
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", escaped)


def _json_scalar(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported final report JSON value: {type(value).__name__}")


def _pareceres_json(item: FinalReportRecord) -> str:
    return json.dumps(
        [asdict(annotation) for annotation in item.pareceres],
        default=_json_scalar,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def _csv_line(values: Iterable[object]) -> str:
    stream = StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerow(
        format(value, ".2f")
        if isinstance(value, Decimal)
        else value.isoformat()
        if isinstance(value, date)
        else value
        for value in values
    )
    return stream.getvalue()


def _csv_record(item: FinalReportRecord) -> str:
    record, decision = item.record, item.decision
    finding = record.finding
    provenance, source = finding.provenance, finding.provenance.source
    values: dict[str, object] = {
        key: getattr(record, key)
        for key in FindingExportRecord.__dataclass_fields__
        if key != "finding"
    }
    values.update(
        {
            "finding_key": finding.finding_key,
            "rule_id": finding.rule_id,
            "rule_version": finding.rule_version,
            "id_documento": finding.id_documento,
            "severity": finding.severity,
            "amount": finding.amount,
            "evidence_json": evidence_json(finding.evidence),
            "status": finding.status,
            "run_id": provenance.run_id,
            "step_id": provenance.step_id,
            "source_file_id": source.source_file_id,
            "source_file": source.source_file,
            "source_sha256": source.source_sha256,
            "record_sha256": source.record_sha256,
            "code_commit": provenance.code_commit,
            "created_at": provenance.created_at,
            "decision_id": decision.decision_id,
            "decision": decision.decision.value,
            "reviewer": decision.reviewer,
            "comment": decision.comment,
            "decided_at": decision.decided_at,
            "decision_run_id": decision.run_id,
            "decision_step_id": decision.step_id,
            "pareceres_json": _pareceres_json(item),
        }
    )
    return _csv_line(values[name] for name in FINAL_CSV_FIELDS)


def _validate(item: FinalReportRecord) -> None:
    finding = item.record.finding
    if (
        finding.status != "active"
        or item.decision.decision != DecisionValue.APPROVED
        or item.decision.finding_key != finding.finding_key
    ):
        raise DomainError("final report requires active, human-approved findings")
    if any(
        annotation.finding_key != finding.finding_key or annotation.kind != "parecer"
        for annotation in item.pareceres
    ):
        raise DomainError("final report contains an unrelated annotation")


def _finding_markdown(item: FinalReportRecord) -> Iterator[str]:
    record, decision = item.record, item.decision
    finding, source = record.finding, record.finding.provenance.source
    yield (
        f"## {_text(finding.rule_id)} — documento {finding.id_documento}\n\n"
        f"Chave do achado: `{finding.finding_key}`. "
        f"Versão da regra: {_text(finding.rule_version)}; "
        f"severidade: {_text(finding.severity)}.\n\n"
        f"Parlamentar no registro original: {_text(record.nome_parlamentar)}; "
        f"UF: {_text(record.sigla_uf)}; partido: {_text(record.sigla_partido)}. "
        f"Competência: {record.mes:02d}/{record.ano}.\n\n"
        f"Fornecedor: {_text(record.fornecedor)}; "
        f"CPF/CNPJ: {_text(record.cnpj_cpf_digits)}; "
        f"número do documento: {_text(record.numero)}; "
        f"emissão: {_text(record.data_emissao)}.\n\n"
        f"Valor sinalizado: {brl(finding.amount)}. "
        f"Valor bruto: {brl(record.valor_documento)}; "
        f"glosa: {brl(record.valor_glosa)}; líquido: {brl(record.valor_liquido)}.\n\n"
        f"URL original (texto): {_text(record.url_documento)}.\n\n"
        f"Evidência determinística: {_text(evidence_json(finding.evidence))}.\n\n"
        "### Aprovação humana\n\n"
        f"Revisor: {_text(decision.reviewer)}; "
        f"data: {_text(decision.decided_at.isoformat())}.\n\n"
        f"Comentário: {_text(decision.comment)}.\n\n"
        f"Decisão: `{decision.decision_id}`; execução: `{decision.run_id}`; "
        f"etapa: `{decision.step_id}`.\n\n"
        "### Proveniência original\n\n"
        f"Fonte: {_text(source.source_file)}; "
        f"identificador: `{source.source_file_id}`; "
        f"registro: {record.source_record_number}; "
        f"tamanho: {record.source_size_bytes} bytes.\n\n"
        f"SHA-256 do arquivo: `{source.source_sha256}`; "
        f"SHA-256 do registro: `{source.record_sha256}`.\n\n"
        f"Execução do achado: `{finding.provenance.run_id}`; "
        f"etapa: `{finding.provenance.step_id}`; "
        f"código: {_text(finding.provenance.code_commit)}; "
        f"criação: {_text(finding.provenance.created_at.isoformat())}.\n\n"
        "### Pareceres preservados\n\n"
    )
    if not item.pareceres:
        yield "Nenhum parecer disponível para este achado.\n\n"
    for annotation in item.pareceres:
        output = annotation.output
        simulated = (
            " (simulado; sem avaliação por modelo real)"
            if annotation.model == "scripted"
            else ""
        )
        yield (
            f"Parecer `{annotation.parecer_id}`; "
            f"rastreio `{annotation.agent_trace_id}`. "
            f"Agente: {_text(annotation.agent_id)}; modelo: {_text(annotation.model)}"
            f"{simulated}; versão do prompt: {_text(annotation.prompt_version)}.\n\n"
            f"Data: {_text(annotation.created_at.isoformat())}; "
            f"execução: `{annotation.run_id}`; etapa: `{annotation.step_id}`.\n\n"
            f"Recomendação consultiva: {_text(output.get('recomendacao'))}; "
            f"confiança: {_text(output.get('confianca'))}.\n\n"
            f"Fundamento: {_text(output.get('fundamento'))}.\n\n"
            f"Evidências citadas: {_text(output.get('evidencias_citadas'))}.\n\n"
        )


def _deputy_markdown(
    run_id: str, deputy_id: int, records: list[FinalReportRecord]
) -> Iterator[str]:
    yield (
        f"# Achados aprovados — deputado {deputy_id}\n\n"
        f"Execução de exportação: `{run_id}`.\n\n"
        "Somente achados ativos com a última decisão humana aprovado, "
        "na fotografia de dados desta exportação. Os pareceres são consultivos; "
        "suas recomendações não substituem a decisão humana.\n\n"
        f"{_LIMITATIONS}\n\n"
    )
    for item in records:
        yield from _finding_markdown(item)
    yield "[Resumo por regra](final.md). [Dados completos em CSV](final.csv).\n"


def _record_artifact(run: Run, artifact: Artifact) -> None:
    artifacts = run.metadata["artifacts"]
    assert isinstance(artifacts, list)
    entry: dict[str, JsonValue] = {
        "path": artifact.path,
        "media_type": artifact.media_type,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }
    artifacts.append(entry)
    run.counters["artifacts_written"] = run.counters.get("artifacts_written", 0) + 1
    run.emit("artifact.created", entry)


def _csv_chunks(
    records: Iterator[FinalReportRecord],
    totals: RuleTotals,
    run: Run,
    writer: ArtifactWriter,
) -> Iterator[str]:
    yield _csv_line(FINAL_CSV_FIELDS)
    previous: int | None = None
    for deputy_id, group in groupby(
        records, key=lambda item: item.record.numero_deputado_id
    ):
        if previous is not None and deputy_id <= previous:
            raise DomainError("final report input must be ordered by deputy")
        previous = deputy_id
        deputy_records: list[FinalReportRecord] = []
        for item in group:
            _validate(item)
            deputy_records.append(item)
            yield _csv_record(item)
            finding = item.record.finding
            key = finding.rule_id, finding.rule_version
            count, amount = totals.get(key, (0, Decimal("0.00")))
            totals[key] = count + 1, amount + finding.amount
            source = finding.provenance.source
            hashes, versions = (
                run.metadata["source_file_hashes"],
                run.metadata["rule_versions"],
            )
            assert isinstance(hashes, dict) and isinstance(versions, dict)
            hashes[f"{source.source_file_id}:{source.source_file}"] = (
                source.source_sha256
            )
            versions[finding.rule_id] = finding.rule_version
        artifact = writer.write_text(
            f"deputado-{deputy_id}.md",
            _deputy_markdown(run.run_id, deputy_id, deputy_records),
            media_type="text/markdown",
        )
        _record_artifact(run, artifact)
        run.counters["deputies_exported"] += 1


def _summary(run: Run, totals: RuleTotals) -> Iterator[str]:
    yield (
        "# Relatório final de achados aprovados\n\n"
        f"Execução de exportação: `{run.run_id}`.\n\n"
        "Inclui somente achados ativos cuja última decisão humana é aprovado. "
        "Achados sem revisão, rejeitados, marcados revisar ou superados foram "
        "excluídos. Pareceres de agentes são anotações consultivas.\n\n"
        f"{_LIMITATIONS}\n\n"
        "| Regra | Versão | Quantidade | Valor sinalizado aprovado |\n"
        "| --- | --- | ---: | ---: |\n"
    )
    for (rule_id, version), (count, amount) in sorted(totals.items()):
        yield f"| {_text(rule_id)} | {_text(version)} | {count} | {brl(amount)} |\n"
    if not totals:
        yield "\nNenhum achado ativo com aprovação humana encontrado.\n"
    yield "\n## Relatórios por deputado\n\n"
    artifacts = run.metadata["artifacts"]
    assert isinstance(artifacts, list)
    for artifact in artifacts:
        assert isinstance(artifact, dict)
        path = str(artifact["path"])
        name = path.rsplit("/", 1)[-1]
        if re.fullmatch(r"deputado--?\d+\.md", name):
            deputy_id = name.removeprefix("deputado-").removesuffix(".md")
            yield f"- [Deputado {deputy_id}]({name}).\n"
    yield "\n[Dados, decisões e pareceres completos em CSV](final.csv).\n"


def export_final_report(
    run: Run, reader: FinalReportReader, writer: ArtifactWriter
) -> None:
    """Stream the approved snapshot, retaining only one deputy's Markdown input."""
    run.require_step()
    run.metadata["approval_scope"] = "active_latest_aprovado"
    run.metadata["parecer_scope"] = "all_successful_explanations"
    run.counters["deputies_exported"] = 0
    totals: RuleTotals = {}
    records = reader.approved_findings()
    try:
        artifact = writer.write_text(
            "final.csv",
            _csv_chunks(records, totals, run, writer),
            media_type="text/csv",
        )
    finally:
        close = getattr(records, "close", None)
        if callable(close):
            close()
    _record_artifact(run, artifact)
    run.counters["findings_exported"] = sum(count for count, _ in totals.values())
    run.counters["rules_exported"] = len(totals)
    _record_artifact(
        run,
        writer.write_text(
            "final.md", _summary(run, totals), media_type="text/markdown"
        ),
    )
    run.emit(
        "final.exported",
        {
            "findings": run.counters["findings_exported"],
            "deputies": run.counters["deputies_exported"],
            "rules": len(totals),
            "approval_scope": "active_latest_aprovado",
        },
    )

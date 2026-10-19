"""Streaming findings export and a Portuguese summary over typed ports."""

import csv
from collections.abc import Iterable, Iterator
from datetime import date
from decimal import Decimal
from io import StringIO

from opsiu.application.ports import (
    Artifact,
    ArtifactWriter,
    FindingExportReader,
    FindingExportRecord,
    JsonValue,
)
from opsiu.application.runs import Run
from opsiu.domain.findings import evidence_json
from opsiu.domain.money import brl

CSV_FIELDS = (
    "finding_key",
    "rule_id",
    "rule_version",
    "id_documento",
    "severity",
    "amount",
    "evidence_json",
    "status",
    "numero_deputado_id",
    "nome_parlamentar",
    "sigla_uf",
    "sigla_partido",
    "ano",
    "mes",
    "fornecedor",
    "cnpj_cpf_digits",
    "numero",
    "data_emissao",
    "valor_documento",
    "valor_glosa",
    "valor_liquido",
    "url_documento",
    "run_id",
    "step_id",
    "source_file_id",
    "source_record_number",
    "source_file",
    "source_sha256",
    "source_size_bytes",
    "record_sha256",
    "code_commit",
    "created_at",
)
type RuleTotals = dict[tuple[str, str], tuple[int, Decimal]]


def _csv_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _csv_line(values: Iterable[object]) -> str:
    output = StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerow(
        _csv_value(value) for value in values
    )
    return output.getvalue()


def _csv_chunks(
    records: Iterator[FindingExportRecord], totals: RuleTotals, run: Run
) -> Iterator[str]:
    yield _csv_line(CSV_FIELDS)
    for record in records:
        finding = record.finding
        provenance, source = finding.provenance, finding.provenance.source
        fields: dict[str, object] = {
            key: getattr(record, key)
            for key in FindingExportRecord.__dataclass_fields__
            if key != "finding"
        }
        fields.update(
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
            }
        )
        yield _csv_line(fields[key] for key in CSV_FIELDS)
        key = finding.rule_id, finding.rule_version
        count, total = totals.get(key, (0, Decimal("0.00")))
        totals[key] = count + 1, total + finding.amount
        hashes = run.metadata["source_file_hashes"]
        versions = run.metadata["rule_versions"]
        assert isinstance(hashes, dict) and isinstance(versions, dict)
        # Include the source ID because historical snapshots may share a path.
        hashes[f"{source.source_file_id}:{source.source_file}"] = source.source_sha256
        versions[finding.rule_id] = finding.rule_version


def render_findings_summary(run_id: str, totals: RuleTotals) -> str:
    lines = [
        "# Achados ativos",
        "",
        f"Execução de exportação: `{run_id}`.",
        "",
        "Este arquivo reúne achados ativos, sem filtro de aprovação humana. "
        "Não é o relatório final de achados aprovados.",
        "",
        "Os totais somam valores sinalizados; não representam prejuízo comprovado "
        "nem valores únicos recuperáveis. R07 e R14 contêm excedentes acumulados "
        "que se sobrepõem entre meses. R09 repete o valor bruto nos documentos "
        "de um grupo de possível duplicidade. Regras diferentes também podem "
        "sinalizar a mesma despesa.",
        "",
        "| Regra | Versão | Quantidade | Valor sinalizado |",
        "| --- | --- | ---: | ---: |",
    ]
    for (rule_id, version), (count, amount) in sorted(totals.items()):
        lines.append(f"| {rule_id} | {version} | {count} | {brl(amount)} |")
    if not totals:
        lines.extend(["", "Nenhum achado ativo encontrado."])
    lines.extend(["", "[Dados e proveniência em CSV](findings.csv).", ""])
    return "\n".join(lines)


def _record_artifact(run: Run, artifact: Artifact) -> None:
    artifacts = run.metadata["artifacts"]
    assert isinstance(artifacts, list)
    description: dict[str, JsonValue] = {
        "path": artifact.path,
        "media_type": artifact.media_type,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }
    artifacts.append(description)
    run.counters["artifacts_written"] = run.counters.get("artifacts_written", 0) + 1
    run.emit("artifact.created", description)


def export_findings(
    run: Run, reader: FindingExportReader, writer: ArtifactWriter
) -> None:
    """Export inside the caller's active step, retaining completed partial outputs."""
    run.require_step()
    totals: RuleTotals = {}
    records = reader.active_findings()
    try:
        csv_artifact = writer.write_text(
            "findings.csv", _csv_chunks(records, totals, run), media_type="text/csv"
        )
    finally:
        close = getattr(records, "close", None)
        if callable(close):
            close()
    _record_artifact(run, csv_artifact)
    run.counters["findings_exported"] = sum(count for count, _ in totals.values())
    run.counters["rules_exported"] = len(totals)
    summary = writer.write_text(
        "findings.md",
        (render_findings_summary(run.run_id, totals),),
        media_type="text/markdown",
    )
    _record_artifact(run, summary)
    run.emit(
        "findings.exported",
        {
            "findings": run.counters["findings_exported"],
            "rules": len(totals),
            "status": "active",
        },
    )

"""Approved export boundaries, exact text and partial artifact persistence."""

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from opsiu.adapters.files.artifacts import FileArtifactWriter
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.final_report import FINAL_CSV_FIELDS, export_final_report
from opsiu.application.ports import (
    Artifact,
    FinalReportRecord,
    FindingExportRecord,
    ReviewAnnotation,
)
from opsiu.application.runs import Run
from opsiu.domain.decisions import Decision, DecisionValue
from opsiu.domain.findings import Finding, Provenance, SourceOrigin

pytestmark = pytest.mark.unit
NOW = datetime(2099, 1, 1, tzinfo=UTC)
UNTRUSTED = "<script>alert(1)</script>\n# Fake title\n[link](javascript:evil)"


class Reader:
    def __init__(self, records: tuple[FinalReportRecord, ...], *, fail: bool = False):
        self.records, self.fail, self.closed = records, fail, False

    def approved_findings(self) -> Iterator[FinalReportRecord]:
        try:
            yield from self.records
            if self.fail:
                raise ValueError("source unavailable")
        finally:
            self.closed = True


def make_run(tmp_path: Path) -> Run:
    return Run(
        ledger=JsonlLedger(tmp_path / "runs"),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="report final",
        arguments={},
        metadata=collect_metadata("settings", tmp_path),
    )


def item(*, deputy: int = 7, document: int = 42) -> FinalReportRecord:
    finding = Finding(
        "R09",
        "1",
        document,
        "media",
        Decimal("90071992547409.91"),
        {"texto": UNTRUSTED, "valor": Decimal("90071992547409.91")},
        Provenance(
            "check-run",
            "check-step",
            SourceOrigin("source", "original.json", "a" * 64, "b" * 64),
            "commit",
            NOW,
        ),
    )
    record = FindingExportRecord(
        finding=finding,
        numero_deputado_id=deputy,
        nome_parlamentar=UNTRUSTED,
        sigla_uf="SP",
        sigla_partido=None,
        ano=2099,
        mes=1,
        fornecedor=UNTRUSTED,
        cnpj_cpf_digits="00000000000001",
        numero="000042",
        data_emissao=None,
        valor_documento=Decimal("90071992547409.91"),
        valor_glosa=Decimal("0.01"),
        valor_liquido=Decimal("90071992547409.90"),
        url_documento=None,
        source_record_number=8,
        source_size_bytes=12345,
    )
    decision = Decision(
        "decision",
        finding.finding_key,
        DecisionValue.APPROVED,
        UNTRUSTED,
        UNTRUSTED,
        NOW,
        "decision-run",
        "decision-step",
    )
    annotation = ReviewAnnotation(
        "opinion",
        finding.finding_key,
        "trace",
        "explainer",
        "parecer",
        "scripted",
        "1",
        {
            "fundamento": UNTRUSTED,
            "recomendacao": "descartar",
            "confianca": 0.5,
            "evidencias_citadas": [UNTRUSTED],
        },
        NOW,
        "annotation-run",
        "annotation-step",
    )
    return FinalReportRecord(record, decision, (annotation,))


def test_exact_csv_safe_markdown_and_independent_provenance(tmp_path: Path) -> None:
    original = item()
    reader, run = Reader((original,)), make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_final_report(run, reader, FileArtifactWriter(tmp_path / "runs", run))
    directory = tmp_path / "runs" / run.run_id
    with (directory / "final.csv").open(newline="", encoding="utf-8") as source:
        parsed = csv.DictReader(source)
        rows = list(parsed)
        assert parsed.fieldnames == list(FINAL_CSV_FIELDS)
    assert len(rows) == 1
    row = rows[0]
    assert row["nome_parlamentar"] == row["comment"] == UNTRUSTED
    assert row["amount"] == "90071992547409.91"
    assert row["valor_liquido"] == "90071992547409.90"
    assert row["numero"] == "000042"
    assert row["run_id"] == "check-run"
    assert row["decision_run_id"] == "decision-run"
    annotation = json.loads(row["pareceres_json"])[0]
    assert annotation["run_id"] == "annotation-run"
    assert annotation["output"]["fundamento"] == UNTRUSTED
    assert annotation["output"]["recomendacao"] == "descartar"
    markdown = (directory / "deputado-7.md").read_text()
    assert "<script>" not in markdown and "\n# Fake title" not in markdown
    assert "[link](javascript:evil)" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "descartar" in markdown and "simulado" in markdown
    assert "check-run" in markdown and "decision-run" in markdown
    summary = (directory / "final.md").read_text()
    assert "| R09 | 1 | 1 | R$ 90.071.992.547.409,91 |" in summary
    assert "(deputado-7.md)" in summary
    assert reader.closed and run.status == "completed"
    assert run.counters == {
        "deputies_exported": 1,
        "findings_exported": 1,
        "rules_exported": 1,
        "artifacts_written": 3,
    }


def test_empty_approved_snapshot_keeps_header_and_summary(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_final_report(
                run, Reader(()), FileArtifactWriter(tmp_path / "runs", run)
            )
    directory = tmp_path / "runs" / run.run_id
    assert (directory / "final.csv").read_text().splitlines() == [
        ",".join(FINAL_CSV_FIELDS)
    ]
    summary = (directory / "final.md").read_text()
    assert "Nenhum achado ativo com aprovação humana" in summary
    assert "prejuízo comprovado" in summary and "R07 e R14" in summary
    assert not list(directory.glob("deputado-*.md"))
    assert run.counters["findings_exported"] == 0
    assert len(run.metadata["artifacts"]) == 2  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid", ["rejected", "superseded", "foreign", "invoice"])
def test_invalid_reader_record_fails_without_publishing(
    tmp_path: Path, invalid: str
) -> None:
    value = item()
    if invalid == "rejected":
        value = replace(
            value, decision=replace(value.decision, decision=DecisionValue.REJECTED)
        )
    elif invalid == "superseded":
        value = replace(
            value,
            record=replace(
                value.record, finding=replace(value.record.finding, status="superseded")
            ),
        )
    elif invalid == "foreign":
        value = replace(
            value, decision=replace(value.decision, finding_key="another-key")
        )
    else:
        value = replace(
            value, pareceres=(replace(value.pareceres[0], kind="nota_fiscal"),)
        )
    reader, run = Reader((value,)), make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_final_report(run, reader, FileArtifactWriter(tmp_path / "runs", run))
    assert run.status == "completed_with_errors" and reader.closed
    assert run.metadata["artifacts"] == []
    assert not (tmp_path / "runs" / run.run_id / "final.csv").exists()


def test_late_reader_failure_keeps_only_completed_deputy_artifact(
    tmp_path: Path,
) -> None:
    reader = Reader((item(deputy=7), item(deputy=8, document=43)), fail=True)
    run = make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_final_report(run, reader, FileArtifactWriter(tmp_path / "runs", run))
    directory = tmp_path / "runs" / run.run_id
    assert run.status == "completed_with_errors" and reader.closed
    assert (directory / "deputado-7.md").exists()
    assert not (directory / "deputado-8.md").exists()
    assert not (directory / "final.csv").exists()
    assert not (directory / "final.md").exists()
    artifacts = run.metadata["artifacts"]
    assert isinstance(artifacts, list) and len(artifacts) == 1


def test_summary_failure_retains_all_completed_data_files(tmp_path: Path) -> None:
    class Writer(FileArtifactWriter):
        def write_text(
            self, name: str, chunks: Iterable[str], *, media_type: str
        ) -> Artifact:
            if name == "final.md":
                raise OSError("summary unavailable")
            return super().write_text(name, chunks, media_type=media_type)

    run, reader = make_run(tmp_path), Reader((item(),))
    with run:
        with run.step("exportar"):
            export_final_report(run, reader, Writer(tmp_path / "runs", run))
    assert run.status == "completed_with_errors" and reader.closed
    assert run.counters["artifacts_written"] == 2
    assert run.counters["findings_exported"] == 1
    assert not (tmp_path / "runs" / run.run_id / "final.md").exists()


def test_export_requires_an_active_run_step(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    with pytest.raises(RuntimeError, match="not active"):
        export_final_report(run, Reader(()), FileArtifactWriter(tmp_path / "runs", run))
    assert not (tmp_path / "runs").exists()

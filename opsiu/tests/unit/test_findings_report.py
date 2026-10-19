"""Exact streaming exports, empty summaries and exclusive artifact failure handling."""

import csv
import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from opsiu.adapters.files.artifacts import FileArtifactWriter
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import Artifact, FindingExportRecord
from opsiu.application.report import (
    CSV_FIELDS,
    export_findings,
    render_findings_summary,
)
from opsiu.application.runs import Run
from opsiu.domain.findings import Finding, Provenance, SourceOrigin

pytestmark = pytest.mark.unit


class Reader:
    def __init__(
        self, records: tuple[FindingExportRecord, ...], *, fail: bool = False
    ) -> None:
        self.records = records
        self.fail = fail
        self.closed = False

    def active_findings(self) -> Iterator[FindingExportRecord]:
        try:
            yield from self.records
            if self.fail:
                raise ValueError("original source unavailable")
        finally:
            self.closed = True


def make_run(tmp_path: Path) -> Run:
    return Run(
        ledger=JsonlLedger(tmp_path / "runs"),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="report findings",
        arguments={},
        metadata=collect_metadata("settings", tmp_path),
    )


def record() -> FindingExportRecord:
    finding = Finding(
        "R09",
        "1",
        42,
        "media",
        Decimal("90071992547409.91"),
        {"documentos": [42, 43], "valor": Decimal("90071992547409.91")},
        Provenance(
            "original-run",
            "original-step",
            SourceOrigin("source-one", "input.json", "a" * 64, "b" * 64),
            "original-commit",
            datetime(2099, 1, 1, tzinfo=UTC),
        ),
    )
    return FindingExportRecord(
        finding=finding,
        numero_deputado_id=101,
        nome_parlamentar='Deputada, com "aspas"\ne quebra',
        sigla_uf="DF",
        sigla_partido=None,
        ano=2099,
        mes=1,
        fornecedor="Fornecedor Sintético",
        cnpj_cpf_digits="00000000000001",
        numero="000042",
        data_emissao=date(2099, 1, 1),
        valor_documento=Decimal("90071992547409.91"),
        valor_glosa=Decimal("0.01"),
        valor_liquido=Decimal("90071992547409.90"),
        url_documento=None,
        source_record_number=3,
        source_size_bytes=12345,
    )


def test_csv_round_trips_unicode_quotes_exact_amounts_and_multiple_source_versions(
    tmp_path: Path,
) -> None:
    first = record()
    original = first.finding.provenance
    second = replace(
        first,
        finding=replace(
            first.finding,
            rule_id="R10",
            provenance=replace(
                original,
                source=replace(
                    original.source, source_file_id="source-two", source_sha256="c" * 64
                ),
            ),
        ),
    )
    reader = Reader((first, second))
    run = make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_findings(run, reader, FileArtifactWriter(tmp_path / "runs", run))
    directory = tmp_path / "runs" / run.run_id
    with (directory / "findings.csv").open(encoding="utf-8", newline="") as source:
        csv_reader = csv.DictReader(source)
        exported = list(csv_reader)
        assert csv_reader.fieldnames == list(CSV_FIELDS)
    assert exported[0]["nome_parlamentar"] == first.nome_parlamentar
    assert exported[0]["amount"] == "90071992547409.91"
    assert exported[0]["valor_liquido"] == "90071992547409.90"
    assert exported[0]["numero"] == "000042"
    assert exported[0]["run_id"] == "original-run"
    assert exported[0]["data_emissao"] == "2099-01-01"
    assert json.loads(exported[0]["evidence_json"])["valor"] == "90071992547409.91"
    assert reader.closed
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["source_file_hashes"] == {
        "source-one:input.json": "a" * 64,
        "source-two:input.json": "c" * 64,
    }
    for artifact in manifest["artifacts"]:
        content = Path(artifact["path"]).read_bytes()
        assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
        assert artifact["size_bytes"] == len(content)


def test_empty_export_keeps_csv_headers_and_portuguese_limitations(
    tmp_path: Path,
) -> None:
    run = make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_findings(run, Reader(()), FileArtifactWriter(tmp_path / "runs", run))
    directory = tmp_path / "runs" / run.run_id
    assert (directory / "findings.csv").read_text().splitlines() == [
        ",".join(CSV_FIELDS)
    ]
    summary = (directory / "findings.md").read_text()
    assert "Nenhum achado ativo encontrado" in summary
    assert "não representam prejuízo comprovado" in summary
    assert "R07 e R14" in summary and "R09" in summary
    assert not (directory / "report.md").exists()
    assert run.counters["findings_exported"] == 0


def test_summary_orders_rule_versions_and_formats_exact_per_rule_totals() -> None:
    summary = render_findings_summary(
        "run",
        {
            ("R10", "1"): (2, Decimal("1000.01")),
            ("R01", "2"): (1, Decimal("12.34")),
        },
    )
    assert summary.index("| R01 |") < summary.index("| R10 |")
    assert "| R10 | 1 | 2 | R$ 1.000,01 |" in summary


def test_reader_failure_removes_unpublished_partial_file_and_closes_iterator(
    tmp_path: Path,
) -> None:
    reader = Reader((record(),), fail=True)
    run = make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_findings(run, reader, FileArtifactWriter(tmp_path / "runs", run))
    directory = tmp_path / "runs" / run.run_id
    assert run.status == "completed_with_errors" and reader.closed
    assert not (directory / "findings.csv").exists()
    assert not (directory / "findings.md").exists()
    assert run.metadata["artifacts"] == []


def test_summary_failure_retains_completed_csv_in_failed_run_manifest(
    tmp_path: Path,
) -> None:
    class FailingSummaryWriter(FileArtifactWriter):
        def write_text(
            self, name: str, chunks: Iterable[str], *, media_type: str
        ) -> Artifact:
            if name == "findings.md":
                raise OSError("summary storage unavailable")
            return super().write_text(name, chunks, media_type=media_type)

    run = make_run(tmp_path)
    with run:
        with run.step("exportar"):
            export_findings(
                run, Reader((record(),)), FailingSummaryWriter(tmp_path / "runs", run)
            )
    directory = tmp_path / "runs" / run.run_id
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "completed_with_errors"
    assert len(manifest["artifacts"]) == 1
    assert manifest["artifacts"][0]["path"].endswith("findings.csv")
    assert manifest["counters"]["findings_exported"] == 1
    assert not (directory / "findings.md").exists()


def test_artifact_writer_requires_active_step_and_never_overwrites(
    tmp_path: Path,
) -> None:
    run = make_run(tmp_path)
    writer = FileArtifactWriter(tmp_path / "runs", run)
    with pytest.raises(RuntimeError, match="not active"):
        writer.write_text("file.txt", ("outside",), media_type="text/plain")
    with run:
        with pytest.raises(RuntimeError, match="active run step"):
            writer.write_text("file.txt", ("outside",), media_type="text/plain")
        with run.step("gravar"):
            artifact = writer.write_text(
                "file.txt", ("original",), media_type="text/plain"
            )
            with pytest.raises(FileExistsError):
                writer.write_text("file.txt", ("replacement",), media_type="text/plain")
            assert Path(artifact.path).read_text() == "original"
            for invalid in (
                "../outside.txt",
                "manifest.json",
                "events.jsonl",
                "log.jsonl",
                "report.md",
                ".",
            ):
                with pytest.raises(ValueError):
                    writer.write_text(invalid, ("invalid",), media_type="text/plain")

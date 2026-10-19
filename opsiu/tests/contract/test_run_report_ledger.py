"""Every finalized file ledger writes an exclusive, privacy-safe run report."""

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never

import duckdb
import pytest

from opsiu.adapters.files import jsonl_log
from opsiu.adapters.files.artifacts import FileArtifactWriter
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import Uuid7Generator
from opsiu.application.ports import (
    EventRecord,
    JsonValue,
    RunMetadata,
    RunRecord,
    StepRecord,
)
from opsiu.application.runs import ConfigurationError, Run

pytestmark = pytest.mark.contract
NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = NOW

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def run_for(ledger: JsonlLedger, clock: Clock, command: str = "contract") -> Run:
    return Run(
        ledger=ledger,
        clock=clock,
        ids=Uuid7Generator(clock),
        command=command,
        arguments={},
        metadata=RunMetadata("settings", "commit", False, "3.12", "0.1.0"),
    )


def report(ledger: JsonlLedger, run: Run) -> str:
    directory = ledger.run_dir(run.run_id)
    assert (directory / "manifest.json").is_file()
    return (directory / "report.md").read_text(encoding="utf-8")


def test_completed_file_only_run_generates_portuguese_report(tmp_path: Path) -> None:
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    run = run_for(ledger, clock, "db status")
    with run:
        clock.advance(2.5)
    text = report(ledger, run)
    assert run.run_id in text and "db status" in text
    assert all(
        label in text
        for label in (
            "Execução",
            "Comando",
            "Estado",
            "Início",
            "Conclusão",
            "Duração",
            "Etapas",
            "Contadores",
            "Falhas",
            "Artefatos",
        )
    )
    assert "completed" in text and "2,500 s" in text
    assert run.counters == {} and run.metadata["artifacts"] == []
    assert not list(tmp_path.glob("*.duckdb"))


def test_step_states_counters_failures_and_artifacts_survive_finish(
    tmp_path: Path,
) -> None:
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    run = run_for(ledger, clock, "report findings")
    with run:
        with run.step("exportar"):
            artifact = FileArtifactWriter(tmp_path / "runs", run).write_text(
                "findings.csv", ("conteudo,sintetico\n",), media_type="text/csv"
            )
            description: dict[str, JsonValue] = {
                "path": artifact.path,
                "media_type": artifact.media_type,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
            }
            run.metadata["artifacts"] = [description]
            run.counters["artifacts_written"] = 1
            run.counters["itens"] = 7
            run.emit("artifact.created", description)
            clock.advance(2.5)
        with run.step("ignorada") as step:
            step.skip("Fonte externa ausente.")
            clock.advance(0.125)
        with run.step("falhou"):
            clock.advance(0.375)
            raise ValueError("  documento\ninválido  ")
    text = report(ledger, run)
    assert "completed_with_errors" in text and "3,000 s" in text
    for expected in (
        "exportar",
        "ignorada",
        "falhou",
        "completed",
        "skipped",
        "failed",
    ):
        assert expected in text
    assert "2,500 s" in text and "0,125 s" in text and "0,375 s" in text
    assert "Fonte externa ausente" in text
    assert "ValueError" in text and "documento inválido" in text
    assert "itens" in text and "7" in text
    assert "findings.csv" in text and "](findings.csv)" in text
    assert artifact.sha256 in text and "text/csv" in text
    assert str(artifact.size_bytes) in text
    manifest = json.loads((ledger.run_dir(run.run_id) / "manifest.json").read_text())
    assert manifest["artifacts"] == [description]
    assert manifest["counters"]["artifacts_written"] == 1
    assert run.counters["artifacts_written"] == 1


@pytest.mark.parametrize("inside_step", [False, True])
def test_configuration_abort_still_has_report_and_durable_error(
    tmp_path: Path,
    inside_step: bool,
) -> None:
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    run = run_for(ledger, clock)
    with pytest.raises(ConfigurationError, match="Configuração inválida"):
        with run:
            if inside_step:
                with run.step("validar"):
                    clock.advance(1)
                    raise ConfigurationError("Configuração inválida.")
            else:
                clock.advance(1)
                raise ConfigurationError("Configuração inválida.")
    text = report(ledger, run)
    assert "aborted" in text and "1,000 s" in text
    assert "ConfigurationError" in text and "Configuração inválida" in text
    assert ("validar" in text) is inside_step
    assert run.status == "aborted"


def test_failure_outside_step_is_reported_after_finalization(tmp_path: Path) -> None:
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    run = run_for(ledger, clock)
    with pytest.raises(OSError, match="Fonte indisponível"):
        with run:
            clock.advance(5.125)
            raise OSError("Fonte indisponível.")
    text = report(ledger, run)
    assert "completed_with_errors" in text and "5,125 s" in text
    assert "OSError" in text and "Fonte indisponível" in text
    assert ledger.get_steps(run.run_id) == ()


def test_existing_report_is_never_replaced_and_manifest_is_preserved(
    tmp_path: Path,
) -> None:
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    run = run_for(ledger, clock)
    path = ledger.run_dir(run.run_id) / "report.md"
    original = "Relatório anterior imutável.\n"
    with pytest.raises(FileExistsError):
        with run:
            with run.step("colisao"):
                path.write_text(original, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == original
    assert (path.parent / "manifest.json").is_file()
    with pytest.raises(RuntimeError, match="did not create"):
        ledger.finish_run(run.record)
    assert path.read_text(encoding="utf-8") == original


def test_failed_renderer_removes_only_its_partial_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    run = run_for(ledger, clock)

    def broken_report(
        record: RunRecord,
        steps: Iterable[StepRecord],
        events: Iterable[EventRecord],
    ) -> Iterator[str]:
        assert record.run_id == run.run_id
        yield "# Relatório parcial\n"
        raise OSError("Falha sintética ao renderizar.")

    monkeypatch.setattr(jsonl_log, "render_run_report", broken_report)
    with pytest.raises(OSError, match="Falha sintética"):
        with run:
            with run.step("executar"):
                run.counters["itens"] = 3
    directory = ledger.run_dir(run.run_id)
    assert not (directory / "report.md").exists()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["counters"] == {"itens": 3}
    assert manifest["steps"][0]["status"] == "completed"
    assert (directory / "events.jsonl").is_file()
    assert (directory / "log.jsonl").is_file()
    with pytest.raises(RuntimeError, match="did not create"):
        ledger.finish_run(run.record)


def test_reusing_a_ledger_does_not_mix_runs_or_change_old_reports(
    tmp_path: Path,
) -> None:
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    first = run_for(ledger, clock, "primeiro")
    with first:
        with first.step("Primeira"):
            raise ValueError("Falha exclusiva da primeira execução.")
    original = (ledger.run_dir(first.run_id) / "report.md").read_bytes()
    clock.advance(1)
    second = run_for(ledger, clock, "segundo")
    with second:
        with second.step("Segunda"):
            pass
    text = report(ledger, second)
    assert "segundo" in text and "Segunda" in text
    assert first.run_id not in text and "Primeira" not in text
    assert "Falha exclusiva" not in text
    assert (ledger.run_dir(first.run_id) / "report.md").read_bytes() == original


def test_run_report_never_queries_private_trace_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_database(*args: object, **kwargs: object) -> Never:
        raise AssertionError("run report must not query private trace tables")

    monkeypatch.setattr(duckdb, "connect", forbidden_database)
    ledger, clock = JsonlLedger(tmp_path / "runs"), Clock()
    run = run_for(ledger, clock, "explain")
    trace_id = Uuid7Generator(clock).new_id()
    private_body = "CONTEUDO_PRIVADO_DO_PROMPT_E_DA_NOTA"
    with run:
        with run.step("anotar"):
            private_path = ledger.run_dir(run.run_id) / "private-trace.json"
            private_path.write_text(json.dumps({"content": private_body}))
            run.emit(
                "agent.completed", {"agent_trace_id": trace_id, "status": "succeeded"}
            )
    text = report(ledger, run)
    assert trace_id in text
    assert private_body not in text
    assert "private-trace.json" not in text

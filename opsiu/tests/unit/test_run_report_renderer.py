"""Pure public-ledger reporting, stable formatting and untrusted text handling."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from opsiu.application.ports import EventRecord, RunMetadata, RunRecord, StepRecord
from opsiu.application.run_report import render_run_report

pytestmark = pytest.mark.unit
START = datetime(2026, 1, 1, tzinfo=UTC)
RUN_ID = "00000000-0000-7000-8000-000000000001"
STEP_ID = "00000000-0000-7000-8000-000000000002"
TRACE_ID = "00000000-0000-7000-8000-000000000003"


def record() -> RunRecord:
    return RunRecord(
        RUN_ID,
        "report final",
        {"anos": "2023-2026"},
        RunMetadata("a" * 64, "commit", True, "3.12", "0.1"),
        START,
        status="completed",
        finished_at=START + timedelta(seconds=12.345),
        counters={"z_counter": 2, "a_counter": 1},
        extra={
            "artifacts": [
                {
                    "path": f"runs/{RUN_ID}/final report.csv",
                    "media_type": "text/csv",
                    "sha256": "b" * 64,
                    "size_bytes": 123,
                }
            ]
        },
    )


def test_run_summary_formats_status_duration_counters_and_relative_artifact_links() -> (
    None
):
    run = record()
    step = StepRecord(
        STEP_ID,
        RUN_ID,
        "exportar",
        START,
        "completed",
        START + timedelta(seconds=2),
    )
    result = "".join(render_run_report(run, (step,), ()))
    assert RUN_ID in result and STEP_ID in result
    assert "report final" in result and "concluída (`completed`)" in result
    assert "Duração: 12,345 s" in result and "2,000 s" in result
    assert START.isoformat() in result
    assert result.index("a\\_counter") < result.index("z\\_counter")
    assert "[final report\\.csv](final%20report.csv)" in result
    assert "b" * 64 in result and "123 bytes" in result
    assert "[Manifesto](manifest.json)" in result
    assert "[Eventos](events.jsonl)" in result
    assert "alterações locais: sim" in result
    assert "Nenhuma falha" in result


def test_failure_and_skip_messages_are_escaped_without_unrelated_event_content() -> (
    None
):
    attack = "<script>alert(1)</script>\n# Título falso\n[click](javascript:x)"
    run = replace(record(), command=attack, status="completed_with_errors")
    events = (
        EventRecord(
            "failure",
            RUN_ID,
            STEP_ID,
            "step.failed",
            {
                "error": {"type": "ValueError", "message": attack},
                "private": "failure-private-marker",
            },
            START,
        ),
        EventRecord("skip", RUN_ID, STEP_ID, "step.skipped", {"reason": attack}, START),
        EventRecord(
            "progress",
            RUN_ID,
            STEP_ID,
            "agentrig.progress",
            {
                "body": "PRIVATE-PROMPT-MARKER",
                "agent_trace_id": TRACE_ID,
                "finding_key": "f" * 64,
            },
            START,
        ),
    )
    result = "".join(render_run_report(run, (), iter(events)))
    assert "completed_with_errors" in result and "ValueError" in result
    assert "etapa" in result and "ignorada" in result
    assert "<script>" not in result and "\n# Título falso" not in result
    assert "[click](javascript:x)" not in result
    assert "&lt;script&gt;" in result
    assert "PRIVATE-PROMPT-MARKER" not in result
    assert "failure-private-marker" not in result
    assert TRACE_ID in result and "f" * 64 in result
    assert "[referência nos eventos](events.jsonl)" in result


def test_aborted_run_with_no_steps_contains_configuration_failure() -> None:
    run = replace(record(), status="aborted", counters={}, extra={})
    event = EventRecord(
        "error",
        RUN_ID,
        None,
        "run.failed",
        {
            "error": {"type": "ConfigurationError", "message": "Configuração inválida"},
        },
        START,
    )
    result = "".join(render_run_report(run, (), (event,)))
    assert "interrompida (`aborted`)" in result
    assert "Nenhuma etapa registrada" in result
    assert "Nenhum contador registrado" in result
    assert "Nenhum artefato de produto registrado" in result
    assert "Configuração inválida" in result


@pytest.mark.parametrize("foreign", ["step", "event"])
def test_renderer_rejects_records_from_another_run(foreign: str) -> None:
    step = StepRecord(STEP_ID, "foreign", "step", START)
    event = EventRecord("event", "foreign", None, "run.finished", {}, START)
    with pytest.raises(ValueError, match="another run"):
        "".join(
            render_run_report(
                record(),
                (step,) if foreign == "step" else (),
                (event,) if foreign == "event" else (),
            )
        )


def test_external_artifacts_and_malformed_trace_references_cannot_inject_links() -> (
    None
):
    run = replace(
        record(),
        extra={
            "artifacts": [
                {"path": "javascript:alert(1)", "size_bytes": 1},
                {"path": "https://example.invalid/outside.csv", "size_bytes": 2},
                None,
                {"path": 5},
            ]
        },
    )
    event = EventRecord(
        "event",
        RUN_ID,
        None,
        "annotation.saved",
        {
            "agent_trace_id": "[x](javascript:x)",
            "finding_key": "bad-reference",
        },
        START,
    )
    result = "".join(render_run_report(run, (), (event,)))
    assert "(javascript:" not in result and "](https://" not in result
    assert "javascript:alert\\(1\\)" in result
    assert "Nenhum achado ou rastro" in result
    assert "bad-reference" not in result


def test_unfinished_values_and_code_delimiters_are_rendered_without_fake_duration() -> (
    None
):
    run = replace(record(), run_id="id`com`marca", status="running", finished_at=None)
    result = "".join(render_run_report(run, (), ()))
    assert "`` id`com`marca ``" in result
    assert "Conclusão: não registrada" in result
    assert "Duração: não finalizada" in result

"""Deterministic bounded samples from one completed check's successful rules."""

from datetime import datetime
from decimal import Decimal

from opsiu.application.ports import (
    FindingExportReader,
    FindingExportRecord,
    FindingSample,
    RunReader,
    RunRecord,
)


def _recency(run: RunRecord) -> tuple[datetime, str]:
    assert run.finished_at is not None
    return run.finished_at, run.run_id


def _priority(record: FindingExportRecord) -> tuple[Decimal, int, str]:
    finding = record.finding
    return finding.amount.copy_negate(), finding.id_documento, finding.finding_key


def sample_findings(
    run_reader: RunReader,
    finding_reader: FindingExportReader,
    *,
    per_rule: int,
) -> FindingSample:
    if type(per_rule) is not int or per_rule < 1:
        raise ValueError("per_rule must be a positive integer")
    latest = max(
        (
            run
            for run in run_reader.list_runs()
            if run.command == "check"
            and run.status in {"completed", "completed_with_errors"}
            and run.finished_at is not None
            and run.arguments.get("dry_run") is not True
        ),
        key=_recency,
        default=None,
    )
    if latest is None:
        raise LookupError("Nenhuma execução concluída de check está disponível.")
    completed_steps = {
        step.step_id
        for step in run_reader.get_steps(latest.run_id)
        if step.run_id == latest.run_id and step.status == "completed"
    }
    rules: set[tuple[str, str]] = set()
    for event in run_reader.get_events(latest.run_id):
        if (
            event.run_id != latest.run_id
            or event.kind != "rule.checked"
            or event.step_id not in completed_steps
            or event.attributes.get("dry_run") is not False
        ):
            continue
        rule_id = event.attributes.get("rule_id")
        version = event.attributes.get("rule_version")
        if (
            isinstance(rule_id, str)
            and rule_id.strip()
            and isinstance(version, str)
            and version.strip()
        ):
            rules.add((rule_id, version))
    if not rules:
        return FindingSample(latest.run_id, ())

    selected: dict[str, list[FindingExportRecord]] = {}
    iterator = finding_reader.active_findings()
    try:
        for record in iterator:
            finding = record.finding
            if (
                finding.status != "active"
                or (
                    finding.rule_id,
                    finding.rule_version,
                )
                not in rules
            ):
                continue
            # Findings retain their first publication run on an idempotent check.
            # Keep only k records per rule while streaming the active inventory.
            records = selected.setdefault(finding.rule_id, [])
            records.append(record)
            records.sort(key=_priority)
            del records[per_rule:]
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
    return FindingSample(
        latest.run_id,
        tuple(record for rule_id in sorted(selected) for record in selected[rule_id]),
    )

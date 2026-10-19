"""Sampling follows the latest check scope and preserves original finding origin."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from weakref import WeakValueDictionary

import pytest

from opsiu.application.ports import (
    EventRecord,
    FindingExportRecord,
    JsonValue,
    RunMetadata,
    RunRecord,
    StepRecord,
    StepStatus,
)
from opsiu.application.sampling import sample_findings
from opsiu.domain.findings import Finding, Provenance, SourceOrigin

pytestmark = pytest.mark.unit
NOW = datetime(2099, 1, 1, tzinfo=UTC)
METADATA = RunMetadata("settings", "commit", False, "3.12", "0.1.0")


class Runs:
    def __init__(
        self,
        runs: tuple[RunRecord, ...],
        steps: tuple[StepRecord, ...] = (),
        events: tuple[EventRecord, ...] = (),
    ) -> None:
        self.runs = runs
        self.steps = steps
        self.events = events
        self.details_requested: list[str] = []

    def list_runs(self) -> tuple[RunRecord, ...]:
        return self.runs

    def get_run(self, run_id: str) -> RunRecord | None:
        return next((run for run in self.runs if run.run_id == run_id), None)

    def get_steps(self, run_id: str) -> tuple[StepRecord, ...]:
        self.details_requested.append(run_id)
        return tuple(step for step in self.steps if step.run_id == run_id)

    def get_events(self, run_id: str) -> tuple[EventRecord, ...]:
        self.details_requested.append(run_id)
        return tuple(event for event in self.events if event.run_id == run_id)


class Findings:
    def __init__(
        self, records: tuple[FindingExportRecord, ...], *, fail: bool = False
    ) -> None:
        self.records = records
        self.fail = fail
        self.opened = False
        self.closed = False

    def active_findings(self) -> Iterator[FindingExportRecord]:
        self.opened = True
        try:
            yield from self.records
            if self.fail:
                raise OSError("synthetic finding read failure")
        finally:
            self.closed = True


def run(run_id: str, *, minute: int = 0) -> RunRecord:
    return RunRecord(
        run_id,
        "check",
        {"dry_run": False},
        METADATA,
        NOW,
        "completed",
        NOW + timedelta(minutes=minute),
    )


def step(
    run_id: str, rule_id: str, *, status: StepStatus = "completed", suffix: str = ""
) -> StepRecord:
    return StepRecord(
        f"{run_id}-{rule_id}{suffix}",
        run_id,
        f"verificar_{rule_id}",
        NOW,
        status,
        NOW,
    )


def event(step: StepRecord, rule_id: str, *, version: str = "1") -> EventRecord:
    return EventRecord(
        f"event-{step.step_id}",
        step.run_id,
        step.step_id,
        "rule.checked",
        {"rule_id": rule_id, "rule_version": version, "dry_run": False},
        NOW,
    )


def record(
    rule_id: str = "R01",
    document: int = 1,
    amount: str = "10.00",
    *,
    version: str = "1",
) -> FindingExportRecord:
    finding = Finding(
        rule_id,
        version,
        document,
        "alta",
        Decimal(amount),
        {},
        Provenance(
            "original-run",
            "original-step",
            SourceOrigin("source-id", "input.json", "a" * 64, "b" * 64),
            "original-commit",
            NOW,
        ),
    )
    return FindingExportRecord(
        finding,
        101,
        "Deputada Sintética",
        "DF",
        None,
        2099,
        1,
        "Fornecedor Sintético",
        "00000000000001",
        str(document),
        date(2099, 1, 1),
        Decimal(amount),
        Decimal("0.00"),
        Decimal(amount),
        "https://example.invalid/invoice.pdf",
        document,
        1000,
    )


def test_latest_partial_check_samples_only_successful_rules_and_original_findings() -> (
    None
):
    older = run("older")
    latest = replace(run("latest", minute=1), status="completed_with_errors")
    completed = step(latest.run_id, "R01")
    failed = step(latest.run_id, "R02", status="failed")
    skipped = step(latest.run_id, "R03", status="skipped")
    old_step = step(older.run_id, "R04")
    ledger = Runs(
        (latest, older),
        (completed, failed, skipped, old_step),
        (
            event(completed, "R01"),
            event(failed, "R02"),
            event(skipped, "R03"),
            event(old_step, "R04"),
        ),
    )
    original = record("R01")
    superseded = replace(
        original, finding=replace(original.finding, status="superseded")
    )
    findings = Findings(
        (
            record("R02"),
            record("R03"),
            record("R04"),
            record("R01", version="2"),
            original,
            superseded,
        )
    )
    sample = sample_findings(ledger, findings, per_rule=20)
    assert sample.check_run_id == latest.run_id
    assert sample.records == (original,)
    assert sample.records[0].finding.provenance.run_id == "original-run"
    assert ledger.details_requested == [latest.run_id, latest.run_id]
    assert findings.closed


def test_dry_runs_aborted_running_unfinished_and_nonchecks_do_not_replace_latest() -> (
    None
):
    eligible = run("eligible")
    more_recent = run("ineligible", minute=1)
    eligible_step = step(eligible.run_id, "R01")
    ledger = Runs(
        (
            replace(more_recent, run_id="dry", arguments={"dry_run": True}),
            replace(more_recent, run_id="aborted", status="aborted"),
            replace(more_recent, run_id="running", status="running"),
            replace(more_recent, run_id="unfinished", finished_at=None),
            replace(more_recent, run_id="other", command="fetch pdf"),
            eligible,
        ),
        (eligible_step,),
        (event(eligible_step, "R01"),),
    )
    finding = record()
    assert sample_findings(ledger, Findings((finding,)), per_rule=1).records == (
        finding,
    )
    assert ledger.details_requested == ["eligible", "eligible"]


def test_latest_check_uses_completion_time_then_run_id_for_ties() -> None:
    late_start = replace(
        run("z-older", minute=1), started_at=NOW + timedelta(seconds=30)
    )
    winner = run("b-latest", minute=2)
    loser = run("a-latest", minute=2)
    ledger = Runs((winner, late_start, loser))
    sample = sample_findings(ledger, Findings(()), per_rule=1)
    assert sample.check_run_id == winner.run_id


def test_latest_without_successful_rule_events_returns_empty_without_fallback() -> None:
    older, latest = run("older"), run("latest", minute=1)
    older_step = step(older.run_id, "R01")
    findings = Findings((record(),))
    sample = sample_findings(
        Runs((older, latest), (older_step,), (event(older_step, "R01"),)),
        findings,
        per_rule=1,
    )
    assert sample.check_run_id == latest.run_id and sample.records == ()
    assert not findings.opened


@pytest.mark.parametrize(
    "attributes",
    [
        {"rule_id": "R01", "rule_version": "1"},
        {"rule_id": "R01", "rule_version": "1", "dry_run": True},
        {"rule_id": "R01", "rule_version": "1", "dry_run": 0},
        {"rule_id": "", "rule_version": "1", "dry_run": False},
        {"rule_id": "R01", "rule_version": "  ", "dry_run": False},
        {"rule_id": 1, "rule_version": "1", "dry_run": False},
        {"rule_id": "R01", "rule_version": 1, "dry_run": False},
    ],
)
def test_rule_event_requires_explicit_non_dry_run_and_nonempty_string_identity(
    attributes: dict[str, JsonValue],
) -> None:
    checked = run("check")
    completed = step(checked.run_id, "R01")
    invalid = replace(event(completed, "R01"), attributes=attributes)
    findings = Findings((record(),))
    sample = sample_findings(
        Runs((checked,), (completed,), (invalid,)), findings, per_rule=1
    )
    assert sample.records == () and not findings.opened


@pytest.mark.parametrize(
    "kind,step_id", [("rule.failed", "check-R01"), ("rule.checked", None)]
)
def test_unrelated_or_detached_events_do_not_authorize_sampling(
    kind: str, step_id: str | None
) -> None:
    checked = run("check")
    completed = step(checked.run_id, "R01")
    invalid = replace(event(completed, "R01"), kind=kind, step_id=step_id)
    assert (
        sample_findings(
            Runs((checked,), (completed,), (invalid,)),
            Findings((record(),)),
            per_rule=1,
        ).records
        == ()
    )


def test_priority_is_exact_amount_desc_document_asc_then_key_with_per_rule_limit() -> (
    None
):
    checked = run("check")
    first = step(checked.run_id, "R01")
    second = step(checked.run_id, "R02")
    additional_version = step(checked.run_id, "R01", suffix="-v2")
    ledger = Runs(
        (checked,),
        (first, second, additional_version),
        (
            event(first, "R01"),
            event(second, "R02"),
            event(additional_version, "R01", version="2"),
        ),
    )
    tied = (record("R01", 2, "50.00"), record("R01", 2, "50.00", version="2"))
    expected_ties = sorted(tied, key=lambda item: item.finding.finding_key)
    best = record("R01", 9, "90071992547409.91")
    lower = record("R01", 1, "90071992547409.90")
    other = record("R02", 1, "20.00")
    unordered = (record("R01", 3, "50.00"), other, tied[1], lower, tied[0], best)
    for candidates in (unordered, tuple(reversed(unordered))):
        findings = Findings(candidates)
        sample = sample_findings(ledger, findings, per_rule=4)
        assert sample.records == (best, lower, *expected_ties, other)
        assert findings.closed


def test_stream_retains_only_the_per_rule_sample() -> None:
    class StreamingFindings:
        def __init__(self) -> None:
            self.live: WeakValueDictionary[int, FindingExportRecord] = (
                WeakValueDictionary()
            )
            self.peak = 0

        def active_findings(self) -> Iterator[FindingExportRecord]:
            for document in range(1000):
                candidate = record(
                    "R01" if document % 2 else "R02", document, f"{document}.00"
                )
                self.live[document] = candidate
                self.peak = max(self.peak, len(self.live))
                yield candidate

    checked = run("check")
    steps = (step(checked.run_id, "R01"), step(checked.run_id, "R02"))
    ledger = Runs((checked,), steps, (event(steps[0], "R01"), event(steps[1], "R02")))
    findings = StreamingFindings()
    sample = sample_findings(ledger, findings, per_rule=3)
    assert [item.finding.id_documento for item in sample.records] == [
        999,
        997,
        995,
        998,
        996,
        994,
    ]
    assert findings.peak <= 8


def test_finding_stream_is_closed_when_reading_fails() -> None:
    checked = run("check")
    completed = step(checked.run_id, "R01")
    findings = Findings((record(),), fail=True)
    with pytest.raises(OSError, match="synthetic finding read failure"):
        sample_findings(
            Runs((checked,), (completed,), (event(completed, "R01"),)),
            findings,
            per_rule=1,
        )
    assert findings.closed


def test_no_eligible_check_raises_without_opening_findings() -> None:
    findings = Findings((record(),))
    with pytest.raises(LookupError, match="check"):
        sample_findings(Runs(()), findings, per_rule=1)
    assert not findings.opened


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "1", None])
def test_sample_size_must_be_a_positive_integer(value: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        sample_findings(Runs(()), Findings(()), per_rule=cast(int, value))

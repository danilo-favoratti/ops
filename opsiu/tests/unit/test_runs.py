"""Run lifecycle behavior using only injected, deterministic in-memory ports."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from opsiu.adapters.system import Uuid7Generator
from opsiu.application.ports import EventRecord, RunMetadata, RunRecord, StepRecord
from opsiu.application.runs import ConfigurationError, Run

pytestmark = pytest.mark.unit


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 18, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"id-{self.value}"


class MemoryLedger:
    def __init__(self) -> None:
        self.runs: list[RunRecord] = []
        self.steps: dict[str, StepRecord] = {}
        self.events: list[EventRecord] = []

    def start_run(self, run: RunRecord) -> None:
        self.runs.append(deepcopy(run))

    def finish_run(self, run: RunRecord) -> None:
        self.runs.append(deepcopy(run))

    def start_step(self, step: StepRecord) -> None:
        self.steps[step.step_id] = deepcopy(step)

    def finish_step(self, step: StepRecord) -> None:
        self.steps[step.step_id] = deepcopy(step)

    def emit(self, event: EventRecord) -> None:
        self.events.append(deepcopy(event))


def build_run(ledger: MemoryLedger) -> Run:
    return Run(
        ledger=ledger,
        clock=FakeClock(),
        ids=SequenceIds(),
        command="test",
        arguments={},
        metadata=RunMetadata("settings", "commit", False, "3.12", "0.1.0"),
    )


def test_nested_failure_continues_and_restores_parent_attribution() -> None:
    ledger = MemoryLedger()
    run = build_run(ledger)
    with run:
        with run.step("parent") as parent:
            assert run.require_step() == parent.record.step_id
            with run.step("child"):
                raise ValueError("  invalid\namount  ")
            assert run.current_step_id == parent.record.step_id
            run.emit("parent.continued")
        with run.step("unavailable") as skipped:
            skipped.skip("Tabela ausente")
        run.counters["processed"] = 2
        run.metadata["rule_versions"] = {"R01": "1"}

    assert run.status == "completed_with_errors"
    assert run.current_step_id is None
    assert [step.status for step in ledger.steps.values()] == [
        "completed",
        "failed",
        "skipped",
    ]
    failure = next(event for event in ledger.events if event.kind == "step.failed")
    child = next(step for step in ledger.steps.values() if step.name == "child")
    assert failure.step_id == child.step_id
    assert failure.attributes == {
        "error": {"type": "ValueError", "message": "invalid amount"}
    }
    continued = next(
        event for event in ledger.events if event.kind == "parent.continued"
    )
    assert continued.step_id == parent.record.step_id
    assert ledger.runs[0].counters == {}
    assert ledger.runs[-1].counters == {"processed": 2}
    assert ledger.runs[-1].extra["rule_versions"] == {"R01": "1"}
    assert all(step.finished_at is not None for step in ledger.steps.values())


def test_configuration_error_aborts_and_propagates() -> None:
    ledger = MemoryLedger()
    run = build_run(ledger)
    with pytest.raises(ConfigurationError, match="invalid config"):
        with run:
            with run.step("configuration"):
                raise ConfigurationError("invalid config")

    assert run.status == "aborted"
    assert ledger.runs[-1].finished_at is not None
    assert run.current_step_id is None


def test_lifecycle_requires_active_run_and_contexts_cannot_be_reused() -> None:
    ledger = MemoryLedger()
    run = build_run(ledger)
    with pytest.raises(RuntimeError):
        run.emit("outside")
    with run:
        with pytest.raises(RuntimeError):
            run.require_step()
        with run.step("once") as step:
            pass
        with pytest.raises(RuntimeError):
            with step:
                pass
    with pytest.raises(RuntimeError):
        with run:
            pass


@pytest.mark.parametrize("failed_event", ["run.started", "step.started"])
def test_enter_failure_finalizes_records_and_clears_step_stack(
    failed_event: str,
) -> None:
    class FailingOnceLedger(MemoryLedger):
        def emit(self, event: EventRecord) -> None:
            super().emit(event)
            if event.kind == failed_event:
                raise OSError("sink unavailable")

    ledger = FailingOnceLedger()
    run = build_run(ledger)
    with pytest.raises(OSError, match="sink unavailable"):
        with run:
            with run.step("startup"):
                pytest.fail("entry failure must prevent the body from executing")

    assert run.status == "completed_with_errors"
    assert run.current_step_id is None
    assert ledger.runs[-1].finished_at is not None
    assert all(step.status == "failed" for step in ledger.steps.values())
    assert all(step.finished_at is not None for step in ledger.steps.values())


def test_uuid7_has_correct_version_time_and_monotonic_order() -> None:
    clock = FakeClock()
    ids = Uuid7Generator(clock)
    generated = [UUID(ids.new_id()) for _ in range(10)]
    clock.value -= timedelta(days=1)
    generated.append(UUID(ids.new_id()))

    assert generated == sorted(generated)
    assert len(set(generated)) == 11
    assert all(identifier.version == 7 for identifier in generated)
    assert generated[0].int >> 80 == int(
        datetime(2026, 10, 18, tzinfo=UTC).timestamp() * 1000
    )

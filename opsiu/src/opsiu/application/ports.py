"""Typed ports and records shared by run orchestration and ledger adapters."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type RunStatus = Literal["running", "completed", "completed_with_errors", "aborted"]
type StepStatus = Literal["running", "completed", "failed", "skipped"]


@dataclass(frozen=True)
class RunMetadata:
    settings_hash: str
    code_commit: str
    code_dirty: bool
    python_version: str
    package_version: str


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    command: str
    arguments: dict[str, JsonValue]
    metadata: RunMetadata
    started_at: datetime
    status: RunStatus = "running"
    finished_at: datetime | None = None
    counters: dict[str, int] = field(default_factory=dict)
    extra: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class StepRecord:
    step_id: str
    run_id: str
    name: str
    started_at: datetime
    status: StepStatus = "running"
    finished_at: datetime | None = None


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    run_id: str
    step_id: str | None
    kind: str
    attributes: dict[str, JsonValue]
    created_at: datetime


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> str: ...


class Ledger(Protocol):
    def start_run(self, run: RunRecord) -> None: ...
    def start_step(self, step: StepRecord) -> None: ...
    def finish_step(self, step: StepRecord) -> None: ...
    def emit(self, event: EventRecord) -> None: ...
    def finish_run(self, run: RunRecord) -> None: ...


class RunReader(Protocol):
    def list_runs(self) -> tuple[RunRecord, ...]: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def get_steps(self, run_id: str) -> tuple[StepRecord, ...]: ...
    def get_events(self, run_id: str) -> tuple[EventRecord, ...]: ...

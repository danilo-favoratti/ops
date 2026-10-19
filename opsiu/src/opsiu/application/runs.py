"""Pure run lifecycle: injected time, identifiers, and append-only event sinks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import TracebackType
from typing import Literal

from opsiu.application.ports import (
    Clock,
    EventRecord,
    IdGenerator,
    JsonValue,
    Ledger,
    RunMetadata,
    RunRecord,
    RunStatus,
    StepRecord,
)


class ConfigurationError(ValueError):
    """An invalid configuration prevents the run from continuing."""


def normalized_error(error: BaseException) -> dict[str, JsonValue]:
    return {"type": type(error).__name__, "message": " ".join(str(error).split())}


class Run:
    def __init__(
        self,
        *,
        ledger: Ledger,
        clock: Clock,
        ids: IdGenerator,
        command: str,
        arguments: dict[str, JsonValue],
        metadata: RunMetadata,
    ) -> None:
        self.ledger = ledger
        self.clock = clock
        self.ids = ids
        self.run_id = ids.new_id()
        self.counters: dict[str, int] = {}
        self.metadata: dict[str, JsonValue] = {
            "source_file_hashes": {},
            "rule_versions": {},
            "prompt_versions": {},
            "artifacts": [],
        }
        self._record = RunRecord(
            self.run_id, command, deepcopy(arguments), metadata, clock.now()
        )
        self._steps: list[Step] = []
        self._active = False
        self._used = False
        self._failed = False

    @property
    def status(self) -> RunStatus:
        return self._record.status

    @property
    def current_step_id(self) -> str | None:
        return self._steps[-1].record.step_id if self._steps else None

    @property
    def record(self) -> RunRecord:
        return replace(
            self._record,
            counters=dict(self.counters),
            extra=deepcopy(self.metadata),
        )

    def require_step(self) -> str:
        self._require_active()
        if self.current_step_id is None:
            raise RuntimeError("this operation requires an active run step")
        return self.current_step_id

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("the run is not active")

    def __enter__(self) -> Run:
        if self._used:
            raise RuntimeError("a run can only be entered once")
        self._used = True
        self._active = True
        try:
            self.ledger.start_run(self.record)
            self.emit("run.started", {"command": self._record.command})
        except BaseException as error:
            # Python does not call __exit__ when __enter__ fails.
            try:
                self.__exit__(type(error), error, error.__traceback__)
            except BaseException:
                pass  # Preserve the original error if a failing sink fails again.
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        status: RunStatus = "completed_with_errors" if self._failed else "completed"
        try:
            if exc is not None:
                if isinstance(exc, ConfigurationError) or not isinstance(
                    exc, Exception
                ):
                    status = "aborted"
                else:
                    status = "completed_with_errors"
                self.emit("run.failed", {"error": normalized_error(exc)})
            self.emit("run.finished", {"status": status})
        except Exception:
            if status != "aborted":
                status = "completed_with_errors"
            raise
        finally:
            self._record = replace(
                self._record, status=status, finished_at=self.clock.now()
            )
            self._active = False
            self.ledger.finish_run(self.record)
        return False

    def emit(self, kind: str, attributes: dict[str, JsonValue] | None = None) -> str:
        self._require_active()
        event = EventRecord(
            self.ids.new_id(),
            self.run_id,
            self.current_step_id,
            kind,
            deepcopy(attributes or {}),
            self.clock.now(),
        )
        self.ledger.emit(event)
        return event.event_id

    def step(self, name: str) -> Step:
        self._require_active()
        return Step(self, name)


class Step:
    def __init__(self, run: Run, name: str) -> None:
        self.run = run
        self.record = StepRecord(run.ids.new_id(), run.run_id, name, run.clock.now())
        self._used = False

    def __enter__(self) -> Step:
        self.run._require_active()
        if self._used:
            raise RuntimeError("a step can only be entered once")
        self._used = True
        self.run._steps.append(self)
        try:
            self.run.ledger.start_step(self.record)
            self.run.emit("step.started", {"name": self.record.name})
        except BaseException as error:
            try:
                self.__exit__(type(error), error, error.__traceback__)
            except BaseException:
                pass  # Preserve the original error if a failing sink fails again.
            raise
        return self

    def skip(self, reason: str) -> None:
        if self.run.current_step_id != self.record.step_id:
            raise RuntimeError("only the current step can be skipped")
        self.record = replace(self.record, status="skipped")
        self.run.emit("step.skipped", {"reason": reason})

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc is not None:
            self.run._failed = True
            self.record = replace(self.record, status="failed")
        elif self.record.status != "skipped":
            self.record = replace(self.record, status="completed")
        self.record = replace(self.record, finished_at=self.run.clock.now())
        try:
            if exc is not None:
                self.run.emit("step.failed", {"error": normalized_error(exc)})
        finally:
            try:
                self.run.ledger.finish_step(self.record)
            finally:
                try:
                    self.run.emit("step.finished", {"status": self.record.status})
                finally:
                    self.run._steps.pop()
        return isinstance(exc, Exception) and not isinstance(exc, ConfigurationError)

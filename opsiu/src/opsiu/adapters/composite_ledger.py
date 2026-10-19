"""Fan out run operations to every configured ledger sink."""

from collections.abc import Callable

from opsiu.application.ports import EventRecord, Ledger, RunRecord, StepRecord


class CompositeLedger:
    def __init__(self, *sinks: Ledger) -> None:
        self.sinks = sinks

    def _apply(self, operation: Callable[[Ledger], None]) -> None:
        errors: list[Exception] = []
        for sink in self.sinks:
            try:
                operation(sink)
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def start_run(self, run: RunRecord) -> None:
        self._apply(lambda sink: sink.start_run(run))

    def start_step(self, step: StepRecord) -> None:
        self._apply(lambda sink: sink.start_step(step))

    def finish_step(self, step: StepRecord) -> None:
        self._apply(lambda sink: sink.finish_step(step))

    def emit(self, event: EventRecord) -> None:
        self._apply(lambda sink: sink.emit(event))

    def finish_run(self, run: RunRecord) -> None:
        self._apply(lambda sink: sink.finish_run(run))

"""Exclusive run directories, append-only events, manifests, and JSON logging."""

from collections.abc import Generator
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from opsiu.adapters.run_serialization import (
    EVENT_ADAPTER,
    STEP_ADAPTER,
    encode_json,
    event_data,
    json_object,
    run_data,
    run_from_data,
    step_data,
)
from opsiu.application.ports import EventRecord, RunRecord, StepRecord
from opsiu.application.run_report import render_run_report


class JsonlLedger:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir
        self._steps: dict[str, StepRecord] = {}
        self._owned_runs: set[str] = set()

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / str(UUID(run_id))

    def start_run(self, run: RunRecord) -> None:
        self._owned_runs.discard(run.run_id)
        directory = self.run_dir(run.run_id)
        directory.mkdir(parents=True, exist_ok=False)
        self._owned_runs.add(run.run_id)
        (directory / "events.jsonl").touch(exist_ok=False)
        (directory / "log.jsonl").touch(exist_ok=False)

    def start_step(self, step: StepRecord) -> None:
        self._require_owned(step.run_id)
        self._steps[step.step_id] = deepcopy(step)

    def finish_step(self, step: StepRecord) -> None:
        self._require_owned(step.run_id)
        self._steps[step.step_id] = deepcopy(step)

    def emit(self, event: EventRecord) -> None:
        import sys

        self._require_owned(event.run_id)
        directory = self.run_dir(event.run_id)
        payload = event_data(event)
        with (directory / "events.jsonl").open("a", encoding="utf-8") as output:
            output.write(encode_json(payload) + "\n")
        log_line = encode_json({"level": "INFO", **payload})
        with (directory / "log.jsonl").open("a", encoding="utf-8") as output:
            output.write(log_line + "\n")
        print(log_line, file=sys.stderr)

    def finish_run(self, run: RunRecord) -> None:
        self._require_owned(run.run_id)
        steps = tuple(
            step for step in self._steps.values() if step.run_id == run.run_id
        )
        manifest = run_data(run)
        manifest["steps"] = [step_data(step) for step in steps]
        try:
            # Persist the authoritative record even if report generation fails.
            with (self.run_dir(run.run_id) / "manifest.json").open(
                "x", encoding="utf-8"
            ) as output:
                output.write(encode_json(manifest) + "\n")
            self._write_report(run, steps)
        finally:
            self._owned_runs.remove(run.run_id)

    def _report_events(self, run_id: str) -> Generator[EventRecord, None, None]:
        with (self.run_dir(run_id) / "events.jsonl").open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    yield EVENT_ADAPTER.validate_json(line)

    def _write_report(self, run: RunRecord, steps: tuple[StepRecord, ...]) -> None:
        path = self.run_dir(run.run_id) / "report.md"
        created = False
        events = self._report_events(run.run_id)
        try:
            with path.open("x", encoding="utf-8") as output:
                created = True
                for chunk in render_run_report(run, steps, events):
                    output.write(chunk)
        except BaseException as error:
            if created:
                try:
                    path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    error.add_note(f"partial report cleanup failed: {cleanup_error}")
            raise
        finally:
            events.close()

    def _require_owned(self, run_id: str) -> None:
        if run_id not in self._owned_runs:
            raise RuntimeError("this sink did not create the run directory")

    def list_runs(self) -> tuple[RunRecord, ...]:
        records = [
            run_from_data(json_object(path.read_text(encoding="utf-8")))
            for path in self.runs_dir.glob("*/manifest.json")
        ]
        return tuple(sorted(records, key=lambda run: (run.started_at, run.run_id)))

    def get_run(self, run_id: str) -> RunRecord | None:
        path = self.run_dir(run_id) / "manifest.json"
        if not path.is_file():
            return None
        return run_from_data(json_object(path.read_text(encoding="utf-8")))

    def get_steps(self, run_id: str) -> tuple[StepRecord, ...]:
        path = self.run_dir(run_id) / "manifest.json"
        if not path.is_file():
            return ()
        steps = json_object(path.read_text(encoding="utf-8")).get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("manifest steps must be a list")
        return tuple(STEP_ADAPTER.validate_python(step) for step in steps)

    def get_events(self, run_id: str) -> tuple[EventRecord, ...]:
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.is_file():
            return ()
        with path.open(encoding="utf-8") as source:
            return tuple(
                EVENT_ADAPTER.validate_json(line) for line in source if line.strip()
            )

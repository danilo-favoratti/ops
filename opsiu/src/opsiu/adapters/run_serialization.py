"""Shared JSON representations for the run ledger and its file artifacts."""

import json
from typing import cast

from pydantic import TypeAdapter

from opsiu.application.ports import EventRecord, JsonValue, RunRecord, StepRecord

RUN_ADAPTER = TypeAdapter(RunRecord)
STEP_ADAPTER = TypeAdapter(StepRecord)
EVENT_ADAPTER = TypeAdapter(EventRecord)


def json_object(text: str) -> dict[str, JsonValue]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("a JSON object is required")
    return cast(dict[str, JsonValue], value)


def event_data(event: EventRecord) -> dict[str, JsonValue]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "step_id": event.step_id,
        "kind": event.kind,
        "attributes": event.attributes,
        "created_at": event.created_at.isoformat(),
    }


def step_data(step: StepRecord) -> dict[str, JsonValue]:
    return {
        "step_id": step.step_id,
        "run_id": step.run_id,
        "name": step.name,
        "started_at": step.started_at.isoformat(),
        "finished_at": step.finished_at.isoformat() if step.finished_at else None,
        "status": step.status,
    }


def run_data(run: RunRecord) -> dict[str, JsonValue]:
    data = dict(run.extra)
    data.update(
        {
            "run_id": run.run_id,
            "command": run.command,
            "arguments": run.arguments,
            "settings_hash": run.metadata.settings_hash,
            "code_commit": run.metadata.code_commit,
            "code_dirty": run.metadata.code_dirty,
            "python_version": run.metadata.python_version,
            "package_version": run.metadata.package_version,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "status": run.status,
            "counters": {name: value for name, value in run.counters.items()},
        }
    )
    return data


def run_from_data(data: dict[str, JsonValue]) -> RunRecord:
    metadata_keys = (
        "settings_hash",
        "code_commit",
        "code_dirty",
        "python_version",
        "package_version",
    )
    record_keys = (
        "run_id",
        "command",
        "arguments",
        "started_at",
        "finished_at",
        "status",
        "counters",
    )
    return RUN_ADAPTER.validate_python(
        {
            **{key: data[key] for key in record_keys},
            "metadata": {key: data[key] for key in metadata_keys},
            "extra": {
                key: value
                for key, value in data.items()
                if key not in (*metadata_keys, *record_keys, "steps")
            },
        }
    )


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

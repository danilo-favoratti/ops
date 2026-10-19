"""CLI run boundaries, durable artifacts, and DuckDB/JSONL reconciliation."""

import json
import os
import platform
import re
from collections.abc import Iterator
from datetime import datetime
from functools import partial
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner, Result

from opsiu import __version__
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.migration_runner import apply_migrations, load_migrations
from opsiu.interfaces.cli import app
from opsiu.interfaces.cli import db as db_commands
from opsiu.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def cli_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "opsiu.toml"
    config.write_text(
        '[paths]\ndata_dir = "input"\ndb_path = "db/opsiu.duckdb"\n'
        'runs_dir = "runs"\nexternal_dir = "external"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPSIU_CONFIG", str(config))
    return Settings()


def read_json(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def read_jsonl(path: Path) -> Iterator[dict[str, object]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        value: object = json.loads(line)
        assert isinstance(value, dict)
        yield value


def run_directory(result: Result, settings: Settings) -> Path:
    lines = result.stdout.splitlines()
    assert lines, result.exception
    first = re.fullmatch(r"run_id: ([0-9a-f-]+)", lines[0])
    assert first is not None, result.stdout
    run_id = UUID(first.group(1))
    assert run_id.version == 7
    directory = settings.paths.runs_dir / str(run_id)
    assert lines[-1] == f"Relatório: {directory / 'report.md'}", result.stdout
    assert directory.is_dir()
    assert (directory / "manifest.json").is_file()
    assert (directory / "events.jsonl").is_file()
    assert (directory / "log.jsonl").is_file()
    return directory


def test_status_before_migrate_is_a_durable_file_run(cli_settings: Settings) -> None:
    result = CliRunner().invoke(app, ["db", "status"])

    assert result.exit_code == 0, result.exception
    directory = run_directory(result, cli_settings)
    assert "0001_init: pendente" in result.stdout
    assert not cli_settings.paths.db_path.exists()
    manifest = read_json(directory / "manifest.json")
    assert manifest["command"] == "db status"
    assert manifest["status"] == "completed"
    events = list(read_jsonl(directory / "events.jsonl"))
    assert events
    assert all(event["run_id"] == directory.name for event in events)


def test_file_only_history_is_visible_before_database_creation(
    cli_settings: Settings,
) -> None:
    runner = CliRunner()
    status = runner.invoke(app, ["db", "status"])
    assert status.exit_code == 0, status.exception
    target = run_directory(status, cli_settings)

    shown = runner.invoke(app, ["runs", "show", target.name, "--events"])
    assert shown.exit_code == 0, shown.exception
    shown_directory = run_directory(shown, cli_settings)
    assert target.name in shown.stdout
    assert "db status" in shown.stdout
    listed = runner.invoke(app, ["runs", "list"])
    assert listed.exit_code == 0, listed.exception
    run_directory(listed, cli_settings)
    assert target.name in listed.stdout
    assert shown_directory.name in listed.stdout
    assert not cli_settings.paths.db_path.exists()


def test_db_commands_track_distinct_runs_and_reconcile_events(
    cli_settings: Settings,
) -> None:
    runner = CliRunner()
    commands = (["db", "migrate"], ["db", "migrate"], ["db", "status"])
    directories: list[Path] = []
    for arguments in commands:
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.exception
        directories.append(run_directory(result, cli_settings))

    assert len({directory.name for directory in directories}) == len(commands)
    with connect_database(cli_settings.paths.db_path, read_only=True) as connection:
        runs = connection.execute(
            "SELECT run_id, command, status, started_at, finished_at "
            "FROM runs ORDER BY started_at"
        ).fetchall()
        assert len(runs) == len(commands)
        assert [str(row[0]) for row in runs] == [path.name for path in directories]
        assert [row[1] for row in runs] == [" ".join(command) for command in commands]
        assert all(row[2] == "completed" for row in runs)
        assert all(row[4] >= row[3] for row in runs)

        for directory in directories:
            events = list(read_jsonl(directory / "events.jsonl"))
            stored = connection.execute(
                "SELECT event_id, run_id, step_id, kind, attributes_json, created_at "
                "FROM events WHERE run_id = ? ORDER BY created_at, event_id",
                [directory.name],
            ).fetchall()
            assert events
            assert len(events) == len(stored)
            by_id = {str(event["event_id"]): event for event in events}
            assert len(by_id) == len(events)
            for event_id, run_id, step_id, kind, attributes, created_at in stored:
                event = by_id[str(event_id)]
                assert event["run_id"] == str(run_id)
                assert event["step_id"] == (str(step_id) if step_id else None)
                assert event["kind"] == kind
                assert event["attributes"] == json.loads(attributes)
                assert datetime.fromisoformat(str(event["created_at"])) == created_at


def test_manifest_captures_reproducible_command_metadata(
    cli_settings: Settings,
) -> None:
    result = CliRunner().invoke(app, ["db", "migrate"])
    assert result.exit_code == 0, result.exception
    directory = run_directory(result, cli_settings)
    manifest = read_json(directory / "manifest.json")

    assert manifest["run_id"] == directory.name
    assert manifest["command"] == "db migrate"
    assert manifest["arguments"] == {}
    assert manifest["settings_hash"] == cli_settings.settings_hash()
    assert manifest["code_commit"] == "unknown"
    assert manifest["code_dirty"] is False
    assert manifest["python_version"] == platform.python_version()
    assert manifest["package_version"] == __version__
    assert manifest["status"] == "completed"
    assert isinstance(manifest["counters"], dict)
    started_at = datetime.fromisoformat(str(manifest["started_at"]))
    finished_at = datetime.fromisoformat(str(manifest["finished_at"]))
    assert started_at.tzinfo is not None
    assert finished_at >= started_at

    logs = list(read_jsonl(directory / "log.jsonl"))
    assert logs
    assert all(log["run_id"] == directory.name for log in logs)
    assert all("step_id" in log for log in logs)
    mirrored_logs = [json.loads(line) for line in result.stderr.splitlines()]
    assert mirrored_logs == logs


def test_runs_commands_render_persisted_history_and_events(
    cli_settings: Settings,
) -> None:
    runner = CliRunner()
    migrated = runner.invoke(app, ["db", "migrate"])
    assert migrated.exit_code == 0, migrated.exception
    target = run_directory(migrated, cli_settings)

    listed = runner.invoke(app, ["runs", "list"])
    assert listed.exit_code == 0, listed.exception
    listed_directory = run_directory(listed, cli_settings)
    assert target.name in listed.stdout
    assert "db migrate" in listed.stdout

    shown = runner.invoke(app, ["runs", "show", target.name])
    assert shown.exit_code == 0, shown.exception
    shown_directory = run_directory(shown, cli_settings)
    assert target.name in shown.stdout
    assert "db migrate" in shown.stdout
    assert "completed" in shown.stdout

    with_events = runner.invoke(app, ["runs", "show", target.name, "--events"])
    assert with_events.exit_code == 0, with_events.exception
    events_directory = run_directory(with_events, cli_settings)
    target_events = list(read_jsonl(target / "events.jsonl"))
    rendered = [
        json.loads(line)
        for line in with_events.stdout.splitlines()
        if line.startswith("{")
    ]
    assert rendered == target_events

    invocation_ids = {
        path.name
        for path in (target, listed_directory, shown_directory, events_directory)
    }
    assert len(invocation_ids) == 4
    manifest = read_json(events_directory / "manifest.json")
    assert manifest["command"] == "runs show"
    assert manifest["arguments"] == {"run_id": target.name, "events": True}
    with connect_database(cli_settings.paths.db_path, read_only=True) as connection:
        stored_ids = connection.execute("SELECT run_id FROM runs").fetchall()
        assert {str(row[0]) for row in stored_ids} == invocation_ids


def test_missing_run_is_a_recorded_failed_step(cli_settings: Settings) -> None:
    runner = CliRunner()
    migrated = runner.invoke(app, ["db", "migrate"])
    assert migrated.exit_code == 0, migrated.exception

    missing_id = str(UUID(int=1))
    result = runner.invoke(app, ["runs", "show", missing_id])

    assert result.exit_code == 1
    directory = run_directory(result, cli_settings)
    manifest = read_json(directory / "manifest.json")
    assert manifest["status"] == "completed_with_errors"
    failures = [
        event
        for event in read_jsonl(directory / "events.jsonl")
        if event["kind"] == "step.failed"
    ]
    assert len(failures) == 1
    failure = failures[0]
    assert failure["step_id"] is not None
    attributes = failure["attributes"]
    assert isinstance(attributes, dict)
    error = attributes["error"]
    assert isinstance(error, dict)
    assert error["type"] == "LookupError"
    assert missing_id in str(error["message"])
    with connect_database(cli_settings.paths.db_path, read_only=True) as connection:
        assert connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", [directory.name]
        ).fetchone() == ("completed_with_errors",)
        assert connection.execute(
            "SELECT status FROM steps WHERE step_id = ?", [failure["step_id"]]
        ).fetchone() == ("failed",)
        assert connection.execute(
            "SELECT kind FROM events WHERE event_id = ?", [failure["event_id"]]
        ).fetchone() == ("step.failed",)

    shown = runner.invoke(app, ["runs", "show", directory.name])
    assert shown.exit_code == 0, shown.exception
    run_directory(shown, cli_settings)
    assert "completed_with_errors" in shown.stdout
    assert missing_id in shown.stdout


def test_invalid_settings_abort_a_durable_run(
    cli_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPSIU_SAMPLING__PER_RULE", "0")

    result = CliRunner().invoke(app, ["db", "migrate"])

    assert result.exit_code == 2
    directory = run_directory(result, cli_settings)
    manifest = read_json(directory / "manifest.json")
    assert manifest["status"] == "aborted"
    assert manifest["settings_hash"] == "unknown"
    assert not cli_settings.paths.db_path.exists()
    failures = [
        event
        for event in read_jsonl(directory / "events.jsonl")
        if event["kind"] == "run.failed"
    ]
    assert len(failures) == 1
    assert "ConfigurationError" in json.dumps(failures[0]["attributes"])


def test_initial_migration_failure_remains_inspectable_without_schema(
    cli_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations = tmp_path / "broken_migrations"
    migrations.mkdir()
    initial = load_migrations()[0]
    (migrations / "0001_init.sql").write_text(
        initial.sql + "\nSELECT * FROM missing_bootstrap_table;", encoding="utf-8"
    )
    monkeypatch.setattr(
        db_commands, "apply_migrations", partial(apply_migrations, directory=migrations)
    )
    runner = CliRunner()

    failed = runner.invoke(app, ["db", "migrate"])

    assert failed.exit_code == 1
    directory = run_directory(failed, cli_settings)
    assert read_json(directory / "manifest.json")["status"] == "completed_with_errors"
    with connect_database(cli_settings.paths.db_path, read_only=True) as connection:
        assert connection.execute("SHOW TABLES").fetchall() == []

    shown = runner.invoke(app, ["runs", "show", directory.name, "--events"])
    assert shown.exit_code == 0, shown.exception
    run_directory(shown, cli_settings)
    assert "completed_with_errors" in shown.stdout
    assert "missing_bootstrap_table" in shown.stdout

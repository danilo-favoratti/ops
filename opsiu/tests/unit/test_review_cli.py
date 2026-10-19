"""Review startup is audited and server tests never open a network listener."""

import json
import os
from importlib import import_module
from pathlib import Path
from uuid import UUID

import pytest
from duckdb import DuckDBPyConnection
from typer.testing import CliRunner, Result

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.interfaces.cli import app
from opsiu.interfaces.cli.runtime import CommandRun
from opsiu.settings import Settings

pytestmark = pytest.mark.unit
review_cli = import_module("opsiu.interfaces.cli.review")


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in os.environ:
        if name.startswith("OPSIU_") or name == "AGENTRIG_RUN_LIVE":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "opsiu.toml"
    config.write_text(
        '[paths]\ndb_path="db/opsiu.duckdb"\nruns_dir="runs"\n'
        'external_dir="external"\n[review]\nreviewer="Revisora Sintética"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPSIU_CONFIG", str(config))
    return Settings()


class Server:
    def __init__(self) -> None:
        self.application = object()
        self.settings: list[Settings] = []
        self.calls: list[tuple[object, str, int]] = []
        self.failure: Exception | None = None

    def create_app(self, settings: Settings) -> object:
        self.settings.append(settings)
        return self.application

    def run(
        self, application: object, *, host: str, port: int, **options: object
    ) -> None:
        self.calls.append((application, host, port))
        assert not options.get("reload")
        if self.failure is not None:
            raise self.failure


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Server:
    mock = Server()
    monkeypatch.setattr(review_cli, "create_app", mock.create_app)
    monkeypatch.setattr(review_cli.uvicorn, "run", mock.run)
    return mock


def manifest(result: Result, settings: Settings) -> tuple[Path, dict[str, object]]:
    lines = result.stdout.splitlines()
    assert lines and lines[0].startswith("run_id: "), result.exception or result.output
    run_id = lines[0].removeprefix("run_id: ")
    assert UUID(run_id).version == 7
    directory = settings.paths.runs_dir / run_id
    assert lines[-1] == f"Relatório: {directory / 'report.md'}"
    assert (directory / "events.jsonl").is_file()
    assert (directory / "log.jsonl").is_file()
    value: object = json.loads((directory / "manifest.json").read_text())
    assert isinstance(value, dict)
    assert value["command"] == "review"
    assert value["run_id"] == run_id
    assert value["finished_at"] is not None
    return directory, value


def events(directory: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in (directory / "events.jsonl").read_text().splitlines():
        value: object = json.loads(line)
        assert isinstance(value, dict)
        records.append(value)
    return records


@pytest.mark.parametrize(
    ("port", "expected"), [(None, 8765), ("9876", 9876), ("1", 1), ("65535", 65535)]
)
def test_review_binds_loopback_and_migrates_inside_its_run(
    settings: Settings,
    server: Server,
    port: str | None,
    expected: int,
) -> None:
    arguments = ["review"] if port is None else ["review", "--port", port]
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.exception or result.output
    directory, run = manifest(result, settings)
    assert server.calls == [(server.application, "127.0.0.1", expected)]
    assert len(server.settings) == 1
    assert server.settings[0].settings_hash() == settings.settings_hash()
    assert run["status"] == "completed"
    assert run["arguments"] == {"port": str(expected)}
    with connect_database(settings.paths.db_path, read_only=True) as connection:
        assert connection.execute(
            "SELECT status FROM runs WHERE run_id=?", [directory.name]
        ).fetchone() == ("completed",)
        assert connection.execute(
            "SELECT name, status FROM steps WHERE run_id=? "
            "ORDER BY started_at, step_id",
            [directory.name],
        ).fetchall() == [
            ("preparar_revisao", "completed"),
            ("servir_revisao", "completed"),
        ]
        assert connection.execute("SELECT count(*) FROM decisions").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM events WHERE run_id=?", [directory.name]
        ).fetchone() == (len(events(directory)),)


@pytest.mark.parametrize("port", ["0", "65536", "-1", "invalid", "1.5", ""])
def test_invalid_port_is_rejected_after_starting_a_durable_run(
    settings: Settings,
    server: Server,
    port: str,
) -> None:
    result = CliRunner().invoke(app, ["review", "--port", port])
    assert result.exit_code == 2, result.exception or result.output
    directory, run = manifest(result, settings)
    assert run["arguments"] == {"port": port}
    assert run["status"] == "aborted"
    assert any(event["kind"] == "run.failed" for event in events(directory))
    assert not server.calls and not server.settings
    assert not settings.paths.db_path.exists()


@pytest.mark.parametrize("where", ["application", "server"])
def test_startup_failure_finishes_both_ledgers_without_false_success(
    settings: Settings,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    if where == "server":
        server.failure = OSError("  porta\nindisponível  ")
    else:

        def fail_app(settings: Settings) -> object:
            raise OSError("  porta\nindisponível  ")

        monkeypatch.setattr(review_cli, "create_app", fail_app)
    result = CliRunner().invoke(app, ["review"])
    assert result.exit_code == 1, result.exception or result.output
    directory, run = manifest(result, settings)
    assert run["status"] == "completed_with_errors"
    with connect_database(settings.paths.db_path, read_only=True) as connection:
        assert connection.execute(
            "SELECT status FROM runs WHERE run_id=?", [directory.name]
        ).fetchone() == ("completed_with_errors",)
        assert connection.execute(
            "SELECT status FROM steps WHERE run_id=? AND name='servir_revisao'",
            [directory.name],
        ).fetchone() == ("failed",)
        assert connection.execute("SELECT count(*) FROM decisions").fetchone() == (0,)
    failed = [event for event in events(directory) if event["kind"] == "step.failed"]
    assert len(failed) == 1
    assert failed[0]["attributes"] == {
        "error": {"type": "OSError", "message": "porta indisponível"},
    }
    assert len(server.calls) == (1 if where == "server" else 0)


@pytest.mark.parametrize("bootstrapped", [False, True])
def test_migration_failure_is_audited_and_does_not_start_server(
    settings: Settings,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
    bootstrapped: bool,
) -> None:
    def fail_migrations(connection: DuckDBPyConnection) -> tuple[str, ...]:
        if bootstrapped:
            apply_migrations(connection)
        raise OSError("Falha sintética na migração.")

    monkeypatch.setattr(review_cli, "apply_migrations", fail_migrations)
    result = CliRunner().invoke(app, ["review"])
    assert result.exit_code == 1, result.exception or result.output
    directory, run = manifest(result, settings)
    assert run["status"] == "completed_with_errors"
    assert not server.calls and not server.settings
    failed = [event for event in events(directory) if event["kind"] == "step.failed"]
    assert len(failed) == 1
    assert "Falha sintética na migração." in json.dumps(failed, ensure_ascii=False)
    if bootstrapped:
        with connect_database(settings.paths.db_path, read_only=True) as connection:
            assert connection.execute(
                "SELECT status FROM runs WHERE run_id=?", [directory.name]
            ).fetchone() == ("completed_with_errors",)


def test_database_open_failure_remains_in_file_ledger(
    settings: Settings,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(self: CommandRun, *, create: bool = False) -> None:
        assert create and self.run.require_step()
        raise OSError("Banco indisponível.")

    monkeypatch.setattr(CommandRun, "open_database", fail_open)
    result = CliRunner().invoke(app, ["review"])
    assert result.exit_code == 1, result.exception or result.output
    directory, run = manifest(result, settings)
    assert run["status"] == "completed_with_errors"
    assert any(event["kind"] == "step.failed" for event in events(directory))
    assert not server.calls and not server.settings
    assert not settings.paths.db_path.exists()

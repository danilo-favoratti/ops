"""Final report startup failures retain audit evidence without initializing data."""

import json
import os
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner, Result

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.files.artifacts import FileArtifactWriter
from opsiu.application.ports import Artifact
from opsiu.interfaces.cli import app
from opsiu.interfaces.cli.runtime import CommandRun
from opsiu.settings import Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in os.environ:
        if name.startswith("OPSIU_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "opsiu.toml"
    config.write_text(
        '[paths]\ndb_path="db/opsiu.duckdb"\nruns_dir="runs"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPSIU_CONFIG", str(config))
    return Settings()


def failed_run(
    result: Result, settings: Settings
) -> tuple[Path, list[dict[str, object]]]:
    assert result.exit_code == 1, result.exception or result.output
    lines = result.stdout.splitlines()
    assert lines and lines[0].startswith("run_id: ")
    run_id = lines[0].removeprefix("run_id: ")
    assert UUID(run_id).version == 7
    directory = settings.paths.runs_dir / run_id
    assert lines[-1] == f"Relatório: {directory / 'report.md'}"
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["command"] == "report final"
    assert manifest["arguments"] == {}
    assert manifest["status"] == "completed_with_errors"
    assert manifest["finished_at"] is not None
    assert len(manifest["steps"]) == 1
    assert manifest["steps"][0]["name"] == "exportar_relatorio_final"
    assert manifest["steps"][0]["status"] == "failed"
    assert manifest["artifacts"] == []
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text().splitlines()
    ]
    assert len([event for event in events if event["kind"] == "step.failed"]) == 1
    assert not any(event["kind"] == "artifact.created" for event in events)
    assert not any(event["kind"] == "final.exported" for event in events)
    return directory, events


@pytest.mark.parametrize("database_exists", [False, True])
def test_missing_database_or_schema_fails_without_auto_migration(
    settings: Settings,
    database_exists: bool,
) -> None:
    if database_exists:
        with connect_database(settings.paths.db_path):
            pass
    result = CliRunner().invoke(app, ["report", "final"])
    directory, _ = failed_run(result, settings)
    assert not list(directory.glob("*.csv"))
    assert settings.paths.db_path.exists() is database_exists
    if database_exists:
        with connect_database(settings.paths.db_path, read_only=True) as connection:
            assert connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='main'"
            ).fetchone() == (0,)


def test_database_open_failure_is_recorded_before_any_export(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(self: CommandRun, *, create: bool = False) -> None:
        assert not create and self.run.require_step()
        raise OSError("Banco indisponível para exportação.")

    monkeypatch.setattr(CommandRun, "open_database", fail_open)
    result = CliRunner().invoke(app, ["report", "final"])
    _, events = failed_run(result, settings)
    failed = next(event for event in events if event["kind"] == "step.failed")
    assert failed["attributes"] == {
        "error": {"type": "OSError", "message": "Banco indisponível para exportação."},
    }
    assert not settings.paths.db_path.exists()


def test_artifact_failure_finishes_database_and_file_runs_without_publication(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with connect_database(settings.paths.db_path) as connection:
        apply_migrations(connection)
        before = connection.execute(
            "SELECT migration_id, applied_at FROM schema_migrations "
            "ORDER BY migration_id"
        ).fetchall()

    def fail_write(
        self: FileArtifactWriter,
        name: str,
        chunks: Iterable[str],
        *,
        media_type: str,
    ) -> Artifact:
        assert self.run.require_step()
        raise OSError("Destino do relatório indisponível.")

    monkeypatch.setattr(FileArtifactWriter, "write_text", fail_write)
    result = CliRunner().invoke(app, ["report", "final"])
    directory, records = failed_run(result, settings)
    assert "Destino do relatório indisponível." in json.dumps(
        records, ensure_ascii=False
    )
    with connect_database(settings.paths.db_path, read_only=True) as connection:
        assert connection.execute(
            "SELECT status FROM runs WHERE run_id=?", [directory.name]
        ).fetchone() == ("completed_with_errors",)
        assert connection.execute(
            "SELECT count(*) FROM events WHERE run_id=?", [directory.name]
        ).fetchone() == (len(records),)
        assert connection.execute("SELECT count(*) FROM decisions").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM findings").fetchone() == (0,)
        assert (
            connection.execute(
                "SELECT migration_id, applied_at FROM schema_migrations "
                "ORDER BY migration_id"
            ).fetchall()
            == before
        )

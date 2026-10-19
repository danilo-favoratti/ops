"""CLI composition root, starting durable runs before opening the database."""

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import typer
from duckdb import DuckDBPyConnection

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import JsonValue, RunReader, RunRecord
from opsiu.application.runs import ConfigurationError, Run
from opsiu.settings import Settings


@dataclass
class CommandRun:
    settings: Settings
    run: Run
    files: JsonlLedger
    database_ledger: DuckDBLedger
    resources: ExitStack
    connection: DuckDBPyConnection | None = None

    def open_database(self, *, create: bool = False) -> DuckDBPyConnection | None:
        self.run.require_step()
        if self.connection is not None:
            return self.connection
        if not create and not self.settings.paths.db_path.exists():
            return None
        self.connection = self.resources.enter_context(
            connect_database(self.settings.paths.db_path)
        )
        if DuckDBLedger.schema_exists(self.connection):
            self.database_ledger.attach(self.connection)
        return self.connection

    def list_runs(self) -> tuple[RunRecord, ...]:
        # File-only bootstrap runs remain visible after the database is migrated.
        records = {record.run_id: record for record in self.files.list_runs()}
        if self.database_ledger.connection is not None:
            for record in self.database_ledger.list_runs():
                records.setdefault(record.run_id, record)
        return tuple(sorted(records.values(), key=lambda record: record.started_at))

    def reader_for(self, run_id: str) -> RunReader:
        if self.files.get_run(run_id) is not None:
            return self.files
        return (
            self.database_ledger
            if self.database_ledger.connection is not None
            else self.files
        )


@contextmanager
def command_run(command: str, arguments: dict[str, JsonValue]) -> Iterator[CommandRun]:
    configuration_error: Exception | None = None
    try:
        settings = Settings()
        runs_dir = settings.paths.runs_dir
        settings_hash = settings.settings_hash()
    except (ValueError, OSError) as error:
        configuration_error = error
        settings = None
        runs_dir = Path(os.environ.get("OPSIU_PATHS__RUNS_DIR", "runs"))
        settings_hash = "unknown"

    files = JsonlLedger(runs_dir)
    database_ledger = DuckDBLedger()
    run = Run(
        ledger=CompositeLedger(files, database_ledger),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command=command,
        arguments=arguments,
        metadata=collect_metadata(settings_hash),
    )
    typer.echo(f"run_id: {run.run_id}")
    failed = False
    with ExitStack() as resources:
        try:
            with run:
                if configuration_error is not None:
                    raise ConfigurationError(
                        str(configuration_error)
                    ) from configuration_error
                assert settings is not None
                yield CommandRun(settings, run, files, database_ledger, resources)
        except Exception:
            failed = True
        finally:
            # Full report rendering is introduced in implementation step 6.2.
            typer.echo(f"Relatório: {runs_dir / run.run_id / 'report.md'}")
    if run.status == "aborted":
        raise typer.Exit(2)
    if failed or run.status == "completed_with_errors":
        raise typer.Exit(1)

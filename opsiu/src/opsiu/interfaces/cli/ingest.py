"""Compose streaming sources and transactional repositories for ingestion."""

from typing import Annotated

import typer

from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.repository import (
    DuckDBExpenseRepository,
    DuckDBPolicyRepository,
)
from opsiu.adapters.files.json_source import JsonExpenseSource
from opsiu.application.ingest import ingest as ingest_sources
from opsiu.application.runs import ConfigurationError
from opsiu.interfaces.cli.runtime import command_run


def _years(value: str) -> range:
    parts = value.split("-")
    try:
        if len(parts) == 1:
            first = last = int(parts[0])
        elif len(parts) == 2:
            first, last = (int(part) for part in parts)
        else:
            raise ValueError
        if not 1 <= first <= last <= 9999:
            raise ValueError
    except ValueError as error:
        raise ConfigurationError(
            "Informe um ano ou intervalo válido, como 2023-2026."
        ) from error
    return range(first, last + 1)


def ingest(
    anos: Annotated[
        str | None, typer.Option(help="Ano ou intervalo inclusivo.")
    ] = None,
    force: Annotated[
        bool, typer.Option(help="Carregar uma nova versão mesmo sem alterações.")
    ] = False,
) -> None:
    """Carregar arquivos anuais e preservar a origem de cada despesa."""
    with command_run("ingest", {"anos": anos, "force": force}) as context:
        years = (
            _years(anos)
            if anos is not None
            else range(context.settings.years.from_, context.settings.years.to + 1)
        )
        with context.run.step("preparar_banco") as prepare_step:
            connection = context.open_database(create=True)
            assert connection is not None
            try:
                migrations = apply_migrations(connection)
            finally:
                if DuckDBLedger.schema_exists(connection):
                    context.database_ledger.attach(connection)
            context.run.counters["migrations_applied"] = len(migrations)
            context.run.emit("db.migrated", {"migrations": list(migrations)})
        if prepare_step.record.status == "failed":
            return
        assert connection is not None
        ingest_sources(
            context.run,
            DuckDBPolicyRepository(connection),
            DuckDBExpenseRepository(connection),
            {
                year: JsonExpenseSource(
                    context.settings.paths.data_dir / f"Ano-{year}.json"
                )
                for year in years
            },
            force=force,
        )
        counters = context.run.counters
        typer.echo(
            f"Arquivos carregados: {counters.get('files_loaded', 0)}; "
            f"inalterados: {counters.get('files_skipped', 0)}; "
            f"falhas: {counters.get('files_failed', 0)}; "
            f"despesas: {counters.get('records_loaded', 0)}."
        )

"""Serve the local review interface inside the command's durable run."""

from typing import Annotated

import typer
import uvicorn

from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.application.runs import ConfigurationError
from opsiu.interfaces.cli.runtime import command_run
from opsiu.interfaces.web.app import create_app


def review(
    port: Annotated[str, typer.Option(help="Porta local entre 1 e 65535.")] = "8765",
) -> None:
    """Abrir a revisão humana local dos achados."""
    with command_run("review", {"port": port}) as context:
        try:
            parsed = int(port)
        except ValueError:
            raise ConfigurationError(
                "A porta deve ser um inteiro entre 1 e 65535."
            ) from None
        if not 1 <= parsed <= 65_535:
            raise ConfigurationError("A porta deve ser um inteiro entre 1 e 65535.")
        with context.run.step("preparar_revisao") as preparation:
            connection = context.open_database(create=True)
            assert connection is not None
            try:
                migrations = apply_migrations(connection)
            finally:
                if DuckDBLedger.schema_exists(connection):
                    context.database_ledger.attach(connection)
            context.run.counters["migrations_applied"] = len(migrations)
            context.run.emit("db.migrated", {"migrations": list(migrations)})
        if preparation.record.status == "failed":
            return
        with context.run.step("servir_revisao"):
            typer.echo(f"Revisão local: http://127.0.0.1:{parsed}/findings")
            uvicorn.run(create_app(context.settings), host="127.0.0.1", port=parsed)

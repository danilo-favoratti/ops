"""Database schema commands."""

import typer

from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations, migration_status
from opsiu.interfaces.cli.runtime import command_run

app = typer.Typer(help="Gerenciar o esquema do banco de dados.", no_args_is_help=True)


@app.command()
def migrate() -> None:
    """Aplicar as migrações pendentes em ordem."""
    with command_run("db migrate", {}) as context:
        with context.run.step("migrar_banco"):
            connection = context.open_database(create=True)
            assert connection is not None
            try:
                applied = apply_migrations(connection)
            finally:
                if DuckDBLedger.schema_exists(connection):
                    context.database_ledger.attach(connection)
            context.run.counters["migrations_applied"] = len(applied)
            context.run.emit("db.migrated", {"migrations": list(applied)})
            if applied:
                for migration_id in applied:
                    typer.echo(f"Aplicada: {migration_id}")
            else:
                typer.echo("Nenhuma migração pendente.")


@app.command()
def status() -> None:
    """Mostrar as migrações aplicadas e pendentes."""
    with command_run("db status", {}) as context:
        with context.run.step("consultar_migracoes"):
            statuses = migration_status(context.open_database())
            for migration in statuses:
                state = (
                    f"aplicada em {migration.applied_at.isoformat()}"
                    if migration.applied_at is not None
                    else "pendente"
                )
                typer.echo(f"{migration.migration_id}: {state}")

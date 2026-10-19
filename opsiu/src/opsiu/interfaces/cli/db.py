"""Database schema commands."""

import typer

from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.migration_runner import apply_migrations, migration_status
from opsiu.settings import Settings

app = typer.Typer(help="Gerenciar o esquema do banco de dados.", no_args_is_help=True)


@app.command()
def migrate() -> None:
    """Aplicar as migrações pendentes em ordem."""
    settings = Settings()
    with connect_database(settings.paths.db_path) as connection:
        applied = apply_migrations(connection)
    if applied:
        for migration_id in applied:
            typer.echo(f"Aplicada: {migration_id}")
    else:
        typer.echo("Nenhuma migração pendente.")


@app.command()
def status() -> None:
    """Mostrar as migrações aplicadas e pendentes."""
    settings = Settings()
    if settings.paths.db_path.exists():
        with connect_database(settings.paths.db_path, read_only=True) as connection:
            statuses = migration_status(connection)
    else:
        statuses = migration_status(None)
    for migration in statuses:
        state = (
            f"aplicada em {migration.applied_at.isoformat()}"
            if migration.applied_at is not None
            else "pendente"
        )
        typer.echo(f"{migration.migration_id}: {state}")

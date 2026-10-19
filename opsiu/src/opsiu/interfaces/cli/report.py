"""Run-scoped exports of active audit findings."""

import typer

from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
from opsiu.adapters.files.artifacts import FileArtifactWriter
from opsiu.application.report import export_findings
from opsiu.interfaces.cli.runtime import command_run

app = typer.Typer(help="Exportar resultados da auditoria.", no_args_is_help=True)


@app.command()
def findings() -> None:
    """Exportar achados ativos com a proveniência original em CSV e Markdown."""
    with command_run("report findings", {}) as context:
        with context.run.step("exportar_achados"):
            connection = context.open_database()
            if connection is None:
                raise LookupError(
                    "Banco não encontrado; execute ingest e check primeiro."
                )
            export_findings(
                context.run,
                DuckDBFindingExportReader(connection),
                FileArtifactWriter(context.settings.paths.runs_dir, context.run),
            )
            directory = context.settings.paths.runs_dir / context.run.run_id
            typer.echo(f"Achados: {directory / 'findings.csv'}")
            typer.echo(f"Resumo: {directory / 'findings.md'}")

"""Run-scoped exports of active audit findings."""

import typer

from opsiu.adapters.duckdb.final_report import DuckDBFinalReportReader
from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
from opsiu.adapters.files.artifacts import FileArtifactWriter
from opsiu.application.final_report import export_final_report
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


@app.command()
def final() -> None:
    """Exportar apenas achados ativos com a última decisão humana aprovado."""
    with command_run("report final", {}) as context:
        with context.run.step("exportar_relatorio_final"):
            connection = context.open_database()
            if connection is None:
                raise LookupError(
                    "Banco não encontrado; execute ingest, check e review primeiro."
                )
            export_final_report(
                context.run,
                DuckDBFinalReportReader(connection),
                FileArtifactWriter(context.settings.paths.runs_dir, context.run),
            )
            directory = context.settings.paths.runs_dir / context.run.run_id
            typer.echo(f"Achados aprovados: {directory / 'final.csv'}")
            typer.echo(f"Resumo final: {directory / 'final.md'}")

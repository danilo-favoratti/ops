"""Run history, step status, and event inspection."""

import typer

from opsiu.adapters.run_serialization import encode_json, event_data
from opsiu.interfaces.cli.runtime import command_run

app = typer.Typer(help="Consultar o histórico de execuções.", no_args_is_help=True)


@app.command("list")
def list_runs() -> None:
    """Listar execuções registradas no banco e nos arquivos."""
    with command_run("runs list", {}) as context:
        with context.run.step("listar_execucoes"):
            context.open_database()
            for record in context.list_runs():
                typer.echo(f"{record.run_id}  {record.command}  {record.status}")


@app.command()
def show(run_id: str, events: bool = False) -> None:
    """Mostrar uma execução, suas etapas e falhas; opcionalmente, os eventos."""
    with command_run("runs show", {"run_id": run_id, "events": events}) as context:
        with context.run.step("mostrar_execucao"):
            context.open_database()
            reader = context.reader_for(run_id)
            record = reader.get_run(run_id)
            if record is None:
                raise LookupError(f"Execução não encontrada: {run_id}")
            typer.echo(f"Execução: {record.run_id}")
            typer.echo(f"Comando: {record.command}")
            typer.echo(f"Estado: {record.status}")
            for step in reader.get_steps(run_id):
                typer.echo(f"Etapa: {step.name}  {step.status}")
            for event in reader.get_events(run_id):
                if event.kind in {"step.failed", "run.failed"}:
                    typer.echo(f"Falha: {encode_json(event.attributes)}")
                if events:
                    typer.echo(encode_json(event_data(event)))
            report_path = context.settings.paths.runs_dir / run_id / "report.md"
            typer.echo(f"Relatório da execução: {report_path}")

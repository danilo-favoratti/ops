"""Thin Typer entry point for OPSIU commands."""

import typer

from opsiu.interfaces.cli.check import check
from opsiu.interfaces.cli.db import app as db_app
from opsiu.interfaces.cli.ingest import ingest
from opsiu.interfaces.cli.runs import app as runs_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(runs_app, name="runs")
app.command()(ingest)
app.command()(check)


@app.callback()
def main() -> None:
    """Auditoria de reembolsos da Cota Parlamentar (CEAP)."""

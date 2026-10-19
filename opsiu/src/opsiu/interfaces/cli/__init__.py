"""Thin Typer entry point for OPSIU commands."""

import typer

from opsiu.interfaces.cli.db import app as db_app

app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")


@app.callback()
def main() -> None:
    """Auditoria de reembolsos da Cota Parlamentar (CEAP)."""

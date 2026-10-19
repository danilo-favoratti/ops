"""Thin Typer entry point for OPSIU commands."""

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Auditoria de reembolsos da Cota Parlamentar (CEAP)."""

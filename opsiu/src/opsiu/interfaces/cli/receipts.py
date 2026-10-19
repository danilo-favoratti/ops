"""Compose bounded receipt acquisition for duplicate-document comparison."""

from typing import Annotated

import typer

from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.pdf_repository import DuckDBPdfRepository
from opsiu.adapters.duckdb.receipt_candidates import DuckDBReceiptCandidates
from opsiu.adapters.external.pdf import PdfDocumentSource
from opsiu.adapters.external.snapshots import HttpSnapshotStore
from opsiu.application.receipts import fetch_receipts
from opsiu.application.runs import ConfigurationError
from opsiu.interfaces.cli.runtime import command_run

app = typer.Typer(help="Capturar e comparar comprovantes de possíveis duplicidades.")


@app.command("fetch")
def fetch(
    limit: Annotated[
        int,
        typer.Option(help="Máximo de documentos, de 2 a 200, sem dividir grupos."),
    ] = 20,
    uncached_only: Annotated[
        bool, typer.Option(help="Avançar ignorando grupos inteiramente em cache.")
    ] = False,
) -> None:
    """Capturar PDFs de grupos completos, reutilizando o cache verificado."""
    with command_run(
        "receipts fetch", {"limit": limit, "uncached_only": uncached_only}
    ) as context:
        if not 2 <= limit <= 200:
            raise ConfigurationError("O limite deve estar entre 2 e 200 documentos.")
        with context.run.step("preparar_comprovantes") as preparation:
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
        assert connection is not None
        fetch_receipts(
            context.run,
            DuckDBReceiptCandidates(connection),
            PdfDocumentSource(
                context.settings.paths.external_dir,
                context.run,
                HttpSnapshotStore(
                    context.settings.paths.external_dir, connection, context.run
                ),
            ),
            DuckDBPdfRepository(connection),
            limit=limit,
            uncached_only=uncached_only,
        )
        typer.echo(
            f"Grupos: {context.run.counters['receipt_groups_selected']}; "
            f"PDFs extraídos: {context.run.counters['receipts_extracted']}; "
            f"em cache: {context.run.counters['receipts_cached']}; "
            f"falhas: {context.run.counters['receipts_failed']}."
        )

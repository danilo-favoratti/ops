"""Compose local snapshot storage and external dataset loaders."""

from pathlib import Path
from typing import Annotated

import typer

from opsiu.adapters.duckdb.cnpj_repository import DuckDBCnpjRepository
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.tse_repository import DuckDBTseRepository
from opsiu.adapters.external.cnpj_bulk import CnpjBulkSource
from opsiu.adapters.external.cnpj_snapshots import LocalCnpjSnapshotStore
from opsiu.adapters.external.snapshots import HttpSnapshotStore
from opsiu.adapters.external.tse import TseCandidateSource
from opsiu.application.fetch import fetch_cnpj, fetch_tse
from opsiu.application.runs import ConfigurationError
from opsiu.interfaces.cli.runtime import command_run

app = typer.Typer(help="Capturar e carregar fontes externas rastreáveis.")


@app.command("tse")
def tse(
    ano: Annotated[int, typer.Option(help="Ano eleitoral: 2024 ou 2026.")],
    snapshot_id: Annotated[
        str | None, typer.Option(help="Reutilizar este snapshot local, sem rede.")
    ] = None,
    replay: Annotated[
        bool, typer.Option(help="Reutilizar o snapshot local mais recente, sem rede.")
    ] = False,
) -> None:
    """Carregar candidaturas ordinárias do primeiro turno a partir do TSE."""
    with command_run(
        "fetch tse", {"ano": ano, "snapshot_id": snapshot_id, "replay": replay}
    ) as context:
        if ano not in {2024, 2026}:
            raise ConfigurationError("O ano eleitoral deve ser 2024 ou 2026.")
        if snapshot_id is not None and replay:
            raise ConfigurationError("Use apenas --snapshot-id ou --replay.")
        with context.run.step("preparar_banco") as preparation:
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
        url = (
            context.settings.external.tse_2024_url
            if ano == 2024
            else context.settings.external.tse_2026_url
        )
        fetch_tse(
            context.run,
            HttpSnapshotStore(
                context.settings.paths.external_dir, connection, context.run
            ),
            TseCandidateSource(),
            DuckDBTseRepository(connection),
            year=ano,
            url=str(url),
            snapshot_id=snapshot_id,
            replay=replay,
        )
        typer.echo(
            f"Candidaturas carregadas: "
            f"{context.run.counters.get('tse_candidates_loaded', 0)}; "
            f"fontes inalteradas: {context.run.counters.get('tse_imports_skipped', 0)}."
        )


@app.command("cnpj")
def cnpj(
    from_dir: Annotated[
        Path | None,
        typer.Option("--from", help="Diretório dos arquivos oficiais da Receita."),
    ] = None,
    snapshot_id: Annotated[
        str | None, typer.Option(help="Reutilizar este snapshot local.")
    ] = None,
    replay: Annotated[
        bool, typer.Option(help="Reutilizar o snapshot local mais recente.")
    ] = False,
) -> None:
    """Carregar empresas, sócios e estabelecimentos dos fornecedores atuais."""
    with command_run(
        "fetch cnpj",
        {
            "from": str(from_dir) if from_dir is not None else None,
            "snapshot_id": snapshot_id,
            "replay": replay,
        },
    ) as context:
        if sum((from_dir is not None, snapshot_id is not None, replay)) != 1:
            raise ConfigurationError(
                "Informe exatamente um: --from, --snapshot-id ou --replay."
            )
        with context.run.step("preparar_banco") as preparation:
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
        fetch_cnpj(
            context.run,
            LocalCnpjSnapshotStore(
                context.settings.paths.external_dir, connection, context.run
            ),
            CnpjBulkSource(),
            DuckDBCnpjRepository(connection),
            directory=from_dir,
            snapshot_id=snapshot_id,
            replay=replay,
        )
        typer.echo(
            f"Empresas: {context.run.counters.get('cnpj_companies_loaded', 0)}; "
            f"sócios: {context.run.counters.get('cnpj_partners_loaded', 0)}; "
            "estabelecimentos: "
            f"{context.run.counters.get('cnpj_establishments_loaded', 0)}."
        )

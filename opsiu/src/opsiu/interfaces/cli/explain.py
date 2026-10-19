"""Compose bounded, explicitly simulated or opted-in live annotations."""

import asyncio
import os
from typing import Annotated

import typer

from opsiu.adapters.agents.gateway import AgentRigGateway
from opsiu.adapters.agents.prompts import default_llms_directory
from opsiu.adapters.duckdb.annotation_repository import DuckDBAnnotationRepository
from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.pdf_repository import DuckDBPdfRepository
from opsiu.adapters.files.rule_docs import load_rule_docs
from opsiu.application.explain import explain as explain_findings
from opsiu.application.runs import ConfigurationError
from opsiu.domain.rules import registry
from opsiu.interfaces.cli.check import _selected
from opsiu.interfaces.cli.runtime import command_run


def explain(
    regras: Annotated[
        str | None, typer.Option(help="Regras separadas por vírgula.")
    ] = None,
    amostra: Annotated[
        int | None, typer.Option(help="Máximo de achados por regra.")
    ] = None,
    force: Annotated[
        bool, typer.Option(help="Acrescentar novas anotações mesmo quando já existem.")
    ] = False,
) -> None:
    """Anotar uma amostra de achados sem alterar as decisões das regras."""
    with command_run(
        "explain", {"regras": regras, "amostra": amostra, "force": force}
    ) as context:
        rules = _selected(regras)
        per_rule = (
            amostra if amostra is not None else context.settings.sampling.per_rule
        )
        if per_rule < 1:
            raise ConfigurationError("A amostra por regra deve ser maior que zero.")
        live = os.environ.get("AGENTRIG_RUN_LIVE") == "1"
        if live and os.environ.get(context.settings.agents.live_env_var) != "1":
            raise ConfigurationError(
                f"O modo ao vivo também exige {context.settings.agents.live_env_var}=1."
            )
        context.run.metadata["agent_mode"] = "live" if live else "scripted"
        context.run.metadata["configured_model"] = context.settings.agents.model
        typer.echo(
            "Modo ao vivo: chamadas Codex limitadas pelo orçamento configurado."
            if live
            else "Modo simulado: respostas de teste; nenhuma chamada a um provedor."
        )
        with context.run.step("preparar_anotacoes") as preparation:
            connection = context.open_database(create=True)
            assert connection is not None
            try:
                migrations = apply_migrations(connection)
            finally:
                if DuckDBLedger.schema_exists(connection):
                    context.database_ledger.attach(connection)
            context.run.counters["migrations_applied"] = len(migrations)
            context.run.emit("db.migrated", {"migrations": list(migrations)})
            documents = load_rule_docs(
                default_llms_directory() / "rules", registry.RULES
            )
            gateway = AgentRigGateway(
                connection, context.run, context.settings, live=live
            )
        if preparation.record.status == "failed":
            return
        assert connection is not None
        asyncio.run(
            explain_findings(
                context.run,
                context.database_ledger,
                DuckDBFindingExportReader(connection),
                DuckDBPdfRepository(connection),
                DuckDBAnnotationRepository(connection),
                gateway,
                rule_texts={
                    key: (doc.version, doc.body) for key, doc in documents.items()
                },
                selected_rules=tuple(rule.descriptor.id for rule in rules),
                per_rule=per_rule,
                max_calls=context.settings.agents.max_calls_per_run,
                force=force,
            )
        )
        typer.echo(
            f"Chamadas: {context.run.counters['annotation_calls']}; "
            f"anotações gravadas: {context.run.counters['annotations_saved']}; "
            f"ignoradas: {context.run.counters['annotations_skipped']}; "
            f"falhas: {context.run.counters['annotations_failed']}."
        )

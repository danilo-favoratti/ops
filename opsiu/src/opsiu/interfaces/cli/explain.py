"""Compose bounded, explicitly simulated or opted-in live annotations."""

import asyncio
import os
from typing import Annotated

import typer

from opsiu.adapters.agents.gateway import AgentRigGateway
from opsiu.adapters.agents.prompts import default_llms_directory
from opsiu.adapters.agents.reviewer import DeputyReviewerGateway
from opsiu.adapters.duckdb.annotation_repository import DuckDBAnnotationRepository
from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.pdf_repository import DuckDBPdfRepository
from opsiu.adapters.duckdb.summary_repository import DuckDBSummaryRepository
from opsiu.adapters.files.rule_docs import load_rule_docs
from opsiu.application.consolidate import consolidate
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
    consolidar: Annotated[
        bool, typer.Option(help="Consolidar os pareceres existentes por deputado.")
    ] = False,
    deputado: Annotated[
        int | None, typer.Option(help="Deputado selecionado para a consolidação.")
    ] = None,
) -> None:
    """Anotar uma amostra de achados sem alterar as decisões das regras."""
    with command_run(
        "explain",
        {
            "regras": regras,
            "amostra": amostra,
            "force": force,
            "consolidar": consolidar,
            "deputado": deputado,
        },
    ) as context:
        if deputado is not None and not consolidar:
            raise ConfigurationError("--deputado exige --consolidar.")
        if consolidar and (regras is not None or amostra is not None):
            raise ConfigurationError(
                "--consolidar usa os pareceres existentes e não aceita "
                "--regras ou --amostra."
            )
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
        context.run.metadata["explain_mode"] = (
            "consolidate" if consolidar else "annotate"
        )
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
        if preparation.record.status == "failed":
            return
        assert connection is not None
        if consolidar:
            asyncio.run(
                consolidate(
                    context.run,
                    DuckDBSummaryRepository(connection),
                    DeputyReviewerGateway(
                        connection, context.run, context.settings, live=live
                    ),
                    max_calls=context.settings.agents.max_calls_per_run,
                    deputy_id=deputado,
                    force=force,
                )
            )
            typer.echo(
                f"Chamadas: {context.run.counters['summary_calls']}; "
                f"resumos gravados: {context.run.counters['summaries_saved']}; "
                f"ignorados: {context.run.counters['summaries_skipped']}; "
                f"falhas: {context.run.counters['summaries_failed']}."
            )
            return
        with context.run.step("carregar_contexto_anotacoes") as annotation_context:
            documents = load_rule_docs(
                default_llms_directory() / "rules", registry.RULES
            )
            gateway = AgentRigGateway(
                connection, context.run, context.settings, live=live
            )
        if annotation_context.record.status == "failed":
            return
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

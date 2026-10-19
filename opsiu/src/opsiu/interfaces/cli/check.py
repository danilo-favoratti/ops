"""Compose registered rules and the database check repository."""

from typing import Annotated

import typer

from opsiu.adapters.duckdb.check_repository import DuckDBCheckRepository
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.application.check import check as check_rules
from opsiu.application.runs import ConfigurationError
from opsiu.domain.rules import registry
from opsiu.domain.rules.base import Rule
from opsiu.domain.rules.quota import QuotaEvaluator
from opsiu.interfaces.cli.runtime import command_run


def _selected(value: str | None) -> tuple[Rule, ...]:
    ids = tuple(rule.descriptor.id for rule in registry.RULES)
    if len(ids) != len(set(ids)):
        raise ConfigurationError("O registro contém regras duplicadas.")
    selected = (
        tuple(part.strip() for part in value.split(",")) if value is not None else ids
    )
    if len(selected) != len(set(selected)):
        raise ConfigurationError("A seleção contém regras duplicadas.")
    try:
        return tuple(registry.get_rule(id) for id in selected)
    except KeyError as error:
        raise ConfigurationError(f"Regra desconhecida: {error.args[0]}") from error


def check(
    regras: Annotated[
        str | None, typer.Option(help="Identificadores separados por vírgula.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option(help="Avaliar sem alterar os achados.")
    ] = False,
) -> None:
    """Executar as regras determinísticas selecionadas."""
    with command_run("check", {"regras": regras, "dry_run": dry_run}) as context:
        rules = tuple(
            type(rule)(context.settings.rules.quota_margin_percent)
            if isinstance(rule, QuotaEvaluator)
            else rule
            for rule in _selected(regras)
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
        check_rules(
            context.run, DuckDBCheckRepository(connection), rules, dry_run=dry_run
        )
        typer.echo(
            f"Regras concluídas: {context.run.counters['rules_completed']}; "
            f"ignoradas: {context.run.counters['rules_skipped']}; "
            f"falhas: {context.run.counters['rules_failed']}; "
            f"achados: {context.run.counters['findings_detected']}."
        )

"""Pure Portuguese run reports over public ledger records."""

import html
import json
import re
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import PurePath
from urllib.parse import quote

from opsiu.application.ports import EventRecord, RunRecord, StepRecord

_STATUSES = {
    "running": "em andamento",
    "completed": "concluída",
    "completed_with_errors": "concluída com erros",
    "aborted": "interrompida",
    "failed": "falhou",
    "skipped": "ignorada",
}


def _text(value: object) -> str:
    escaped = html.escape(str(value), quote=False)
    return re.sub(
        r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", " ".join(escaped.splitlines())
    )


def _status(value: str) -> str:
    return f"{_STATUSES[value]} (`{value}`)"


def _code(value: str) -> str:
    text = " ".join(value.splitlines())
    delimiter = "`" * (
        max((len(part) for part in re.findall(r"`+", text)), default=0) + 1
    )
    return f"{delimiter} {text} {delimiter}"


def _time(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "não registrada"


def _duration(start: datetime, finish: datetime | None) -> str:
    if finish is None:
        return "não finalizada"
    seconds = (finish - start).total_seconds()
    return f"{seconds:.3f}".replace(".", ",") + " s"


def _artifacts(run: RunRecord) -> Iterator[str]:
    yield "## Artefatos produzidos\n\n"
    artifacts = run.extra.get("artifacts", [])
    found = False
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not isinstance(
                artifact.get("path"), str
            ):
                continue
            path = str(artifact["path"])
            name = PurePath(path).name
            # Only link a basename inside this run. External snapshot locations
            # remain inert text; their provenance is available in the ledger.
            link = (
                f"[{_text(name)}]({quote(name, safe='')})"
                if PurePath(path).parent.name == run.run_id
                else _text(path)
            )
            yield (
                f"- {link}; "
                f"tipo: {_text(artifact.get('media_type', 'não informado'))}; "
                f"tamanho: {_text(artifact.get('size_bytes', 'não informado'))} bytes; "
                f"SHA-256: {_text(artifact.get('sha256', 'não informado'))}.\n"
            )
            found = True
    if not found:
        yield "Nenhum artefato de produto registrado.\n"
    yield "\n"


def _events(run: RunRecord, events: Iterable[EventRecord]) -> Iterator[str]:
    yield "## Falhas e etapas ignoradas\n\n"
    found = False
    references: set[tuple[str, str]] = set()
    for event in events:
        if event.run_id != run.run_id:
            raise ValueError("run report received an event from another run")
        # Do not serialize arbitrary event attributes: ordinary SDK progress and
        # future integrations may contain fields unsuitable for a public report.
        if event.kind in {"step.failed", "run.failed"}:
            error = event.attributes.get("error")
            if isinstance(error, dict):
                scope = f"etapa {_text(event.step_id)}" if event.step_id else "execução"
                yield (
                    f"- {_time(event.created_at)} — {scope}: "
                    f"{_text(error.get('type', 'Erro'))}: "
                    f"{_text(error.get('message', 'mensagem não registrada'))}.\n"
                )
                found = True
        elif event.kind == "step.skipped":
            yield (
                f"- {_time(event.created_at)} — etapa {_text(event.step_id)} ignorada: "
                f"{_text(event.attributes.get('reason', 'motivo não registrado'))}.\n"
            )
            found = True
        for field in ("agent_trace_id", "finding_key"):
            value = event.attributes.get(field)
            pattern = (
                r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
                if field == "agent_trace_id"
                else r"[0-9a-f]{64}"
            )
            if isinstance(value, str) and re.fullmatch(pattern, value):
                references.add((field, value))
    if not found:
        yield "Nenhuma falha ou etapa ignorada registrada.\n"
    yield "\n## Referências de auditoria\n\n"
    if not references:
        yield "Nenhum achado ou rastro de agente referenciado nos eventos.\n"
    for field, value in sorted(references):
        label = "Rastro de agente" if field == "agent_trace_id" else "Achado"
        yield f"- {label} `{value}` — [referência nos eventos](events.jsonl).\n"
    yield (
        "\nEste relatório usa somente o registro público da execução. "
        "Instruções, entradas e respostas privadas de agentes não são lidas "
        "nem reproduzidas aqui.\n\n"
    )


def render_run_report(
    run: RunRecord,
    steps: Iterable[StepRecord],
    events: Iterable[EventRecord],
) -> Iterator[str]:
    """Render recorded values without database, filesystem or agent access."""
    arguments = json.dumps(run.arguments, ensure_ascii=False, sort_keys=True)
    yield (
        "# Relatório da execução\n\n"
        f"Execução: {_code(run.run_id)}.\n\n"
        f"Comando: {_text(run.command)}.\n\n"
        f"Estado: {_status(run.status)}.\n\n"
        f"Início: {_time(run.started_at)}.\n\n"
        f"Conclusão: {_time(run.finished_at)}.\n\n"
        f"Duração: {_duration(run.started_at, run.finished_at)}.\n\n"
        f"Argumentos: {_text(arguments)}.\n\n"
        "## Etapas\n\n"
        "| Etapa | Identificador | Estado | Início | Conclusão | Duração |\n"
        "| --- | --- | --- | --- | --- | ---: |\n"
    )
    found = False
    for step in steps:
        if step.run_id != run.run_id:
            raise ValueError("run report received a step from another run")
        yield (
            f"| {_text(step.name)} | {_code(step.step_id)} | {_status(step.status)} | "
            f"{_time(step.started_at)} | {_time(step.finished_at)} | "
            f"{_duration(step.started_at, step.finished_at)} |\n"
        )
        found = True
    if not found:
        yield "\nNenhuma etapa registrada.\n"
    yield "\n## Contadores\n\n"
    if not run.counters:
        yield "Nenhum contador registrado.\n"
    for name, value in sorted(run.counters.items()):
        yield f"- {_text(name)}: {value}.\n"
    yield "\n"
    yield from _events(run, events)
    yield from _artifacts(run)
    yield (
        "## Proveniência da execução\n\n"
        f"Configuração (SHA-256): {_text(run.metadata.settings_hash)}.\n\n"
        f"Commit: {_text(run.metadata.code_commit)}; alterações locais: "
        f"{'sim' if run.metadata.code_dirty else 'não'}.\n\n"
        f"Python: {_text(run.metadata.python_version)}; "
        f"OPSIU: {_text(run.metadata.package_version)}.\n\n"
        "[Manifesto](manifest.json) · [Eventos](events.jsonl) · "
        "[Log estruturado](log.jsonl).\n"
    )

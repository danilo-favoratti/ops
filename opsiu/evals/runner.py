"""Run bounded recommendation evaluations against isolated synthetic findings."""

import asyncio
import hashlib
import json
import os
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import cast

from agentrig.core import JsonValue as AgentJson
from agentrig.core.cancellation import CancellationSource
from agentrig.core.clock import SystemClock as AgentClock
from agentrig.core.context import RunContext
from agentrig.core.grading import GradingContext
from agentrig.core.identity import RunId, Uuid4IdGenerator
from duckdb import DuckDBPyConnection

from evals.dataset import (
    CASES_PATH,
    EVALUATION_YEAR,
    FIXTURE_SHA256,
    LOCAL_RULE_IDS,
    EvalCase,
    build_expense_rows,
    load_cases,
)
from evals.graders import RecommendationAgreementGrader, RecommendationComparison
from opsiu.adapters.agents.contracts import ParecerCodec
from opsiu.adapters.agents.gateway import AgentRigGateway
from opsiu.adapters.agents.prompts import default_llms_directory
from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.check_repository import DuckDBCheckRepository
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.findings_export import DuckDBFindingExportReader
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.repository import (
    DuckDBExpenseRepository,
    DuckDBPolicyRepository,
)
from opsiu.adapters.files.artifacts import FileArtifactWriter
from opsiu.adapters.files.json_source import JsonExpenseSource
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.files.rule_docs import RuleDocument, load_rule_docs
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.check import check
from opsiu.application.ingest import ingest
from opsiu.application.ports import (
    AnnotationRequest,
    FindingExportRecord,
    JsonValue,
    RunStatus,
)
from opsiu.application.runs import ConfigurationError, Run
from opsiu.domain.rules import registry
from opsiu.settings import Settings


@dataclass(frozen=True)
class EvaluationResult:
    run_id: str
    run_dir: Path
    dataset_size: int
    selected: int
    attempted: int
    succeeded: int
    failed: int
    matches: int
    agreement: float | None
    coverage: float
    status: RunStatus
    mode: str
    model: str


@dataclass
class _Progress:
    dataset_size: int = 0
    selected: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    matches: int = 0
    dataset_sha256: str | None = None
    cases: list[dict[str, JsonValue]] = field(default_factory=list)

    def summary(self) -> dict[str, JsonValue]:
        return {
            "dataset_size": self.dataset_size,
            "selected": self.selected,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "matches": self.matches,
            "agreement": self.matches / self.attempted if self.attempted else None,
            "coverage": self.succeeded / self.dataset_size
            if self.dataset_size
            else 0.0,
        }


class EvaluationFailure(RuntimeError):
    """Public evaluation failure with private agent details excluded."""


def _write_artifact(
    run: Run, writer: FileArtifactWriter, name: str, text: str, media_type: str
) -> None:
    artifact = writer.write_text(name, (text,), media_type=media_type)
    data: dict[str, JsonValue] = {
        "path": artifact.path,
        "media_type": artifact.media_type,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }
    artifacts = run.metadata["artifacts"]
    assert isinstance(artifacts, list)
    artifacts.append(data)
    run.counters["artifacts_written"] = run.counters.get("artifacts_written", 0) + 1
    run.emit("artifact.created", data)


def _sample(
    records: tuple[FindingExportRecord, ...], cases: tuple[EvalCase, ...], per_rule: int
) -> tuple[tuple[EvalCase, FindingExportRecord], ...]:
    inventory = {record.finding.finding_key: record for record in records}
    missing = [case.finding_key for case in cases if case.finding_key not in inventory]
    if missing:
        raise ConfigurationError(
            f"{len(missing)} rótulo(s) não correspondem aos achados sintéticos."
        )
    by_rule: dict[str, list[tuple[EvalCase, FindingExportRecord]]] = {}
    for case in cases:
        record = inventory[case.finding_key]
        by_rule.setdefault(record.finding.rule_id, []).append((case, record))
    queues = []
    for rule_id in sorted(by_rule):
        ordered = sorted(
            by_rule[rule_id],
            key=lambda item: (
                item[1].finding.amount.copy_negate(),
                item[1].finding.id_documento,
                item[0].finding_key,
            ),
        )
        queues.append(deque(ordered[:per_rule]))
    selected = []
    while any(queues):
        for queue in queues:
            if queue:
                selected.append(queue.popleft())
    return tuple(selected)


def _grading_context(run: Run) -> GradingContext:
    return GradingContext(
        run_context=RunContext(
            run_id=RunId(run.run_id),
            parent_run_id=None,
            clock=AgentClock(),
            id_generator=Uuid4IdGenerator(RunId),
            cancellation=CancellationSource().token,
            correlation={"step_id": run.require_step()},
        )
    )


async def _evaluate(
    run: Run,
    gateway: AgentRigGateway,
    selected: tuple[tuple[EvalCase, FindingExportRecord], ...],
    documents: dict[str, RuleDocument],
    progress: _Progress,
    budget: int,
) -> None:
    grader = RecommendationAgreementGrader()
    for case, record in selected[:budget]:
        finding = record.finding
        actual: str | None = None
        trace_id: str | None = None
        failure: dict[str, JsonValue] | None = None
        progress.attempted += 1
        with run.step(f"avaliar_{finding.rule_id}_{finding.id_documento}"):
            try:
                document = documents[finding.rule_id]
                if document.version != finding.rule_version:
                    raise ValueError("rule document version differs from finding")
                result = await gateway.annotate(
                    AnnotationRequest(record, document.body, None), kind="parecer"
                )
                trace_id = result.trace_id
                if result.failure is not None or result.output is None:
                    raise EvaluationFailure("A anotação não produziu parecer válido.")
                opinion = ParecerCodec().decode(cast(AgentJson, result.output))
                actual = opinion.recomendacao
            except Exception:
                failure = {"kind": "annotation_failed", "trace_id": trace_id}
                actual = None
            grade = await grader.grade(
                RecommendationComparison(case.expected_recommendation, actual),
                _grading_context(run),
            )
            outcome: dict[str, JsonValue] = {
                "finding_key": case.finding_key,
                "rule_id": finding.rule_id,
                "rule_version": finding.rule_version,
                "expected_recommendation": case.expected_recommendation,
                "actual_recommendation": actual,
                "trace_id": trace_id,
                "score": grade.score,
                "failure": failure,
                "grade": cast(dict[str, JsonValue], json.loads(grade.to_json())),
            }
            progress.cases.append(outcome)
            if actual is None:
                progress.failed += 1
            else:
                progress.succeeded += 1
            progress.matches += int(grade.score == 1)
            run.counters.update(
                {
                    "eval_attempted": progress.attempted,
                    "eval_succeeded": progress.succeeded,
                    "eval_failed": progress.failed,
                    "eval_matches": progress.matches,
                }
            )
            run.emit(
                "eval.case_graded",
                {
                    "finding_key": case.finding_key,
                    "agent_trace_id": trace_id,
                    "score": grade.score,
                    "valid_output": actual is not None,
                },
            )
            if failure is not None:
                raise EvaluationFailure(
                    "Falha de anotação; a tentativa recebeu nota zero. "
                    "Consulte o rastro privado quando disponível."
                )
    if progress.attempted < len(selected):
        run.counters["budget_exhausted"] = 1
        run.emit("budget.exhausted", {"max_calls": budget, "calls": progress.attempted})


def _export(
    run: Run, writer: FileArtifactWriter, progress: _Progress, mode: str, model: str
) -> None:
    summary = progress.summary()
    data: dict[str, JsonValue] = {
        "run_id": run.run_id,
        "mode": mode,
        "model": model,
        "dataset_sha256": progress.dataset_sha256,
        **summary,
        "cases": list(progress.cases),
    }
    with run.step("exportar_avaliacao"):
        _write_artifact(
            run,
            writer,
            "eval_results.json",
            json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
            + "\n",
            "application/json",
        )
        agreement = summary["agreement"]
        agreement_text = (
            f"{agreement:.2%}" if isinstance(agreement, float) else "não calculada"
        )
        coverage = (
            progress.succeeded / progress.dataset_size if progress.dataset_size else 0
        )
        text = (
            "# Avaliação de recomendações\n\n"
            f"Execução: `{run.run_id}`.\n\n"
            f"Modo: {'simulado' if mode == 'scripted' else 'ao vivo'}.\n\n"
            f"Casos rotulados: {progress.dataset_size}; "
            f"selecionados: {progress.selected}; tentativas: {progress.attempted}.\n\n"
            f"Saídas válidas: {progress.succeeded}; falhas: {progress.failed}; "
            f"concordâncias: {progress.matches}.\n\n"
            f"Concordância por tentativa: {agreement_text}.\n\n"
            f"Cobertura de saídas válidas sobre todos os rótulos: {coverage:.2%}.\n\n"
            "Falhas contam como zero na concordância; casos não executados ficam "
            "fora desse denominador e reduzem a cobertura. Sem tentativas, "
            "a concordância não é calculada.\n\n"
            "Os rótulos são referências sintéticas de teste, não pareceres "
            "humanos auditados. O cenário simulado emite revisar e não usa os "
            "rótulos esperados para gerar respostas. Esta avaliação não mede "
            "correção jurídica nem aprova achados. Não cobre R11–R13 ou leitura "
            "de PDFs.\n\n"
            "[Resultados e notas por caso](eval_results.json) · "
            "[Rótulos utilizados](labeled_findings.jsonl).\n"
        )
        _write_artifact(run, writer, "eval.md", text, "text/markdown")
        run.metadata["evaluation_summary"] = summary
        run.emit("eval.completed", summary)


def _execute(
    run: Run,
    settings: Settings,
    cases_path: Path,
    run_dir: Path,
    ledger: DuckDBLedger,
    resources: ExitStack,
    progress: _Progress,
    *,
    live: bool,
) -> None:
    writer = FileArtifactWriter(settings.paths.runs_dir, run)
    connection: DuckDBPyConnection | None = None
    with run.step("preparar_avaliacao") as preparation:
        if settings.rules.quota_margin_percent != Decimal("5"):
            raise ConfigurationError("Os rótulos sintéticos exigem margem de 5%.")
        if live and os.environ.get(settings.agents.live_env_var) != "1":
            raise ConfigurationError(
                f"O modo ao vivo também exige {settings.agents.live_env_var}=1."
            )
        try:
            raw_labels = cases_path.read_bytes()
            _write_artifact(
                run,
                writer,
                "labeled_findings.jsonl",
                raw_labels.decode("utf-8"),
                "application/x-ndjson",
            )
            cases = load_cases(run_dir / "labeled_findings.jsonl")
        except (ValueError, OSError) as error:
            raise ConfigurationError(
                "O conjunto de rótulos é inválido ou indisponível."
            ) from error
        progress.dataset_size = len(cases)
        progress.dataset_sha256 = hashlib.sha256(raw_labels).hexdigest()
        source = run_dir / f"Ano-{EVALUATION_YEAR}.json"
        _write_artifact(
            run,
            writer,
            source.name,
            json.dumps({"dados": build_expense_rows()}, ensure_ascii=False) + "\n",
            "application/json",
        )
        run.metadata["evaluation"] = {
            "dataset_sha256": progress.dataset_sha256,
            "fixture_sha256": FIXTURE_SHA256,
            "replicas": 4,
            "source_year": EVALUATION_YEAR,
            "db_path": str(run_dir / "eval.duckdb"),
            "source_path": str(source),
            "per_rule": settings.sampling.per_rule,
            "max_calls": settings.agents.max_calls_per_run,
            "rules": list(LOCAL_RULE_IDS),
        }
        connection = resources.enter_context(connect_database(run_dir / "eval.duckdb"))
        try:
            migrations = apply_migrations(connection)
        finally:
            if DuckDBLedger.schema_exists(connection):
                ledger.attach(connection)
        run.counters["migrations_applied"] = len(migrations)
        run.emit("db.migrated", {"migrations": list(migrations)})
    if preparation.record.status == "failed":
        return
    assert connection is not None
    ingest(
        run,
        DuckDBPolicyRepository(connection),
        DuckDBExpenseRepository(connection),
        {EVALUATION_YEAR: JsonExpenseSource(source)},
    )
    if run.counters.get("records_loaded") != 800 or run.counters.get("files_failed"):
        raise EvaluationFailure("A preparação das 800 despesas sintéticas falhou.")
    check(
        run,
        DuckDBCheckRepository(connection),
        tuple(registry.get_rule(rule_id) for rule_id in LOCAL_RULE_IDS),
    )
    if run.counters["rules_completed"] != len(LOCAL_RULE_IDS):
        raise EvaluationFailure(
            "As regras sintéticas não foram concluídas integralmente."
        )
    with run.step("selecionar_casos") as selection:
        records = tuple(DuckDBFindingExportReader(connection).active_findings())
        selected = _sample(records, cases, settings.sampling.per_rule)
        documents = load_rule_docs(default_llms_directory() / "rules", registry.RULES)
        progress.selected = len(selected)
        run.counters.update(
            {
                "eval_dataset_size": len(cases),
                "eval_selected": len(selected),
                "eval_attempted": 0,
                "eval_succeeded": 0,
                "eval_failed": 0,
                "eval_matches": 0,
                "budget_exhausted": 0,
                "agent_calls": 0,
            }
        )
        run.emit(
            "eval.selected",
            {
                "finding_keys": [case.finding_key for case, _ in selected],
                "per_rule": settings.sampling.per_rule,
            },
        )
    if selection.record.status == "failed":
        return
    gateway = AgentRigGateway(connection, run, settings, live=live)
    asyncio.run(
        _evaluate(
            run,
            gateway,
            selected,
            documents,
            progress,
            settings.agents.max_calls_per_run,
        )
    )
    _export(run, writer, progress, "live" if live else "scripted", gateway.model)


def run_evaluation(
    settings: Settings, cases_path: Path | None = None
) -> EvaluationResult:
    """Create one durable run; configured production data paths are never opened."""
    source = CASES_PATH if cases_path is None else cases_path
    live = os.environ.get("AGENTRIG_RUN_LIVE") == "1"
    mode = "live" if live else "scripted"
    model = settings.agents.model if live else "scripted"
    files, database = JsonlLedger(settings.paths.runs_dir), DuckDBLedger()
    run = Run(
        ledger=CompositeLedger(files, database),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="eval recommendations",
        arguments={"cases_path": str(source)},
        metadata=collect_metadata(settings.settings_hash()),
    )
    run_dir = files.run_dir(run.run_id)
    progress = _Progress()
    run.metadata.update({"agent_mode": mode, "configured_model": settings.agents.model})
    status: RunStatus | None = None
    with ExitStack() as resources:
        try:
            with run:
                _execute(
                    run,
                    settings,
                    source,
                    run_dir,
                    database,
                    resources,
                    progress,
                    live=live,
                )
        except ConfigurationError:
            status = "aborted"
        except Exception:
            status = "completed_with_errors"
    return EvaluationResult(
        run_id=run.run_id,
        run_dir=run_dir,
        dataset_size=progress.dataset_size,
        selected=progress.selected,
        attempted=progress.attempted,
        succeeded=progress.succeeded,
        failed=progress.failed,
        matches=progress.matches,
        agreement=progress.matches / progress.attempted if progress.attempted else None,
        coverage=progress.succeeded / progress.dataset_size
        if progress.dataset_size
        else 0.0,
        status=status or run.status,
        mode=mode,
        model=model,
    )

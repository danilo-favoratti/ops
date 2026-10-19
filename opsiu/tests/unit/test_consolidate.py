"""Summary orchestration accounts for calls and binds exact source opinions."""

import asyncio
import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.consolidate import (
    consolidate,
    summary_agent_input,
    summary_input_hash,
    summary_input_json,
)
from opsiu.application.ports import (
    AnnotationResult,
    DeputyReviewInput,
    SummaryFinding,
    SummarySpec,
)
from opsiu.application.runs import ConfigurationError, Run
from opsiu.domain.findings import Finding, Provenance, SourceOrigin

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=UTC)
SPEC = SummarySpec("scripted", "consolidar-deputado", "1", "f" * 64)


def request(deputy: int) -> DeputyReviewInput:
    finding = Finding(
        "R08",
        "1",
        deputy,
        "alta",
        Decimal("90071992547409.91"),
        {"total": Decimal("90071992547409.91")},
        Provenance(
            "original-run",
            "original-step",
            SourceOrigin("original-source", "Ano-2026.json", "a" * 64, "b" * 64),
            "original-commit",
            NOW,
        ),
    )
    return DeputyReviewInput(
        deputy,
        "Deputada Original",
        (
            SummaryFinding(
                finding,
                deputy,
                "Deputada Original",
                2026,
                1,
                f"opinion-{deputy}",
                f"opinion-trace-{deputy}",
                {"fundamento": "Texto privado", "recomendacao": "descartar"},
            ),
        ),
    )


class Repository:
    def __init__(self, *, cached: bool = False, plain: bool = False) -> None:
        self.cached = cached
        self.plain = plain
        self.closed = False
        self.selected: tuple[str, int | None] | None = None
        self.saved: list[tuple[int, str]] = []
        self.checks: list[int] = []

    def inputs(
        self, model: str, *, deputy_id: int | None = None
    ) -> Iterator[DeputyReviewInput]:
        self.selected = (model, deputy_id)
        records = tuple(request(i) for i in (101, 102) if deputy_id in (None, i))
        if self.plain:
            return iter(records)

        def stream() -> Iterator[DeputyReviewInput]:
            try:
                yield from records
            finally:
                self.closed = True

        return stream()

    def has_success(self, deputy_id: int, input_sha256: str, spec: SummarySpec) -> bool:
        self.checks.append(deputy_id)
        assert spec == SPEC
        assert input_sha256 == summary_input_hash(request(deputy_id))
        return self.cached

    def save(
        self,
        request: DeputyReviewInput,
        input_sha256: str,
        spec: SummarySpec,
        result: AnnotationResult,
        *,
        summary_id: str,
        created_at: datetime,
        run_id: str,
        step_id: str,
    ) -> None:
        assert result.output is not None and result.failure is None
        assert spec == SPEC and summary_id and run_id and step_id
        assert created_at.tzinfo is not None
        self.saved.append((request.numero_deputado_id, input_sha256))


class Reviewer:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.calls: list[int] = []

    def spec(self) -> SummarySpec:
        return SPEC

    async def summarize(self, request: DeputyReviewInput) -> AnnotationResult:
        self.calls.append(request.numero_deputado_id)
        if request.numero_deputado_id == 101:
            if self.failure == "throw":
                raise ValueError("PRIVATE_PROVIDER_RESPONSE")
            if self.failure == "result":
                return AnnotationResult("failed-trace", None, {"private": "PRIVATE"})
        return AnnotationResult("successful-trace", {"simulado": True}, None)


def execute(
    tmp_path: Path,
    repository: Repository,
    reviewer: Reviewer,
    *,
    budget: int = 5,
    force: bool = False,
    deputy: int | None = None,
) -> Run:
    run = Run(
        ledger=JsonlLedger(tmp_path / "runs"),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="explain",
        arguments={"consolidar": True},
        metadata=collect_metadata("settings", tmp_path),
    )
    with run:
        asyncio.run(
            consolidate(
                run,
                repository,
                reviewer,
                max_calls=budget,
                force=force,
                deputy_id=deputy,
            )
        )
    return run


def test_input_hash_is_order_independent_and_binds_opinion_identity() -> None:
    first = request(101)
    second = replace(request(102).findings[0], numero_deputado_id=101)
    combined = replace(first, findings=(*first.findings, second))
    reversed_input = replace(combined, findings=tuple(reversed(combined.findings)))
    assert summary_input_json(combined) == summary_input_json(reversed_input)
    assert (
        summary_input_hash(combined)
        == hashlib.sha256(summary_input_json(combined).encode()).hexdigest()
    )
    changed = replace(first, findings=(replace(first.findings[0], parecer_id="new"),))
    assert summary_agent_input(first) == summary_agent_input(changed)
    assert summary_input_hash(first) != summary_input_hash(changed)
    context = json.loads(summary_input_json(first))
    assert context["agent_input"]["achados"][0]["achado"]["valor_envolvido"] == (
        "90071992547409.91"
    )
    assert (
        context["agent_input"]["achados"][0]["parecer"]["recomendacao"] == "descartar"
    )
    assert context["sources"][0]["source_file_id"] == "original-source"
    assert context["sources"][0]["finding_run_id"] == "original-run"


@pytest.mark.parametrize("budget", [0, 1])
def test_budget_stops_before_next_call_and_closes_iterator(
    tmp_path: Path,
    budget: int,
) -> None:
    repository, reviewer = Repository(), Reviewer()
    run = execute(tmp_path, repository, reviewer, budget=budget)
    assert repository.closed
    assert len(reviewer.calls) == len(repository.saved) == budget
    assert run.counters["summary_calls"] == budget
    assert run.counters["budget_exhausted"] == 1
    events = (tmp_path / "runs" / run.run_id / "events.jsonl").read_text()
    assert events.count('"kind":"budget.exhausted"') == 1


@pytest.mark.parametrize("force", [False, True])
def test_cached_summary_skips_without_spending_budget_unless_forced(
    tmp_path: Path,
    force: bool,
) -> None:
    repository, reviewer = Repository(cached=True, plain=True), Reviewer()
    run = execute(tmp_path, repository, reviewer, force=force)
    assert repository.checks == [101, 102]
    assert reviewer.calls == ([101, 102] if force else [])
    assert run.counters["summaries_skipped"] == (0 if force else 2)
    assert run.counters["summaries_saved"] == (2 if force else 0)
    assert run.status == "completed"


@pytest.mark.parametrize("failure", ["result", "throw"])
def test_failure_consumes_one_call_and_preserves_private_details(
    tmp_path: Path,
    failure: str,
) -> None:
    repository, reviewer = Repository(), Reviewer(failure)
    run = execute(tmp_path, repository, reviewer)
    assert reviewer.calls == [101, 102]
    assert [deputy for deputy, _ in repository.saved] == [102]
    assert run.status == "completed_with_errors"
    assert run.counters["summary_calls"] == 2
    assert run.counters["summaries_failed"] == 1
    events = (tmp_path / "runs" / run.run_id / "events.jsonl").read_text()
    assert "PRIVATE" not in events and "Texto privado" not in events


def test_context_records_exact_opinions_and_deputy_filter(tmp_path: Path) -> None:
    repository, reviewer = Repository(), Reviewer()
    run = execute(tmp_path, repository, reviewer, deputy=102)
    assert repository.selected == ("scripted", 102)
    assert reviewer.calls == [102]
    contexts = run.metadata["summary_inputs"]
    assert isinstance(contexts, list) and len(contexts) == 1
    context = contexts[0]
    assert isinstance(context, dict)
    assert context["input_sha256"] == summary_input_hash(request(102))
    assert context["sources"] == [
        {
            "finding_key": request(102).findings[0].finding.finding_key,
            "parecer_id": "opinion-102",
            "parecer_trace_id": "opinion-trace-102",
        }
    ]


def test_empty_population_completes_without_reviewer_calls(tmp_path: Path) -> None:
    repository, reviewer = Repository(), Reviewer()
    run = execute(tmp_path, repository, reviewer, deputy=999)
    assert reviewer.calls == [] and repository.closed
    assert run.status == "completed" and run.counters["deputies_selected"] == 0
    assert run.counters["budget_exhausted"] == 0


def test_invalid_budget_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="orçamento"):
        execute(tmp_path, Repository(), Reviewer(), budget=-1)

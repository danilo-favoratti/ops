"""Human decisions append through one recorded step per independently owned run."""

from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import Uuid7Generator
from opsiu.application.ports import (
    FindingExportRecord,
    JsonValue,
    ReviewDetail,
    ReviewFilters,
    ReviewItem,
    RunMetadata,
)
from opsiu.application.review import ReviewService
from opsiu.application.runs import Run
from opsiu.domain.decisions import Decision, DecisionValue
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import Finding, Provenance, SourceOrigin
from opsiu.domain.models import Despesa

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RunFactory:
    def __init__(self, path: Path) -> None:
        self.clock = FixedClock()
        self.ids = Uuid7Generator(self.clock)
        self.ledger = JsonlLedger(path / "runs")
        self.runs: list[Run] = []

    def __call__(self, command: str, arguments: dict[str, JsonValue]) -> Run:
        run = Run(
            ledger=self.ledger,
            clock=self.clock,
            ids=self.ids,
            command=command,
            arguments=arguments,
            metadata=RunMetadata("settings", "commit", False, "3.12", "0.1.0"),
        )
        self.runs.append(run)
        return run


def detail() -> ReviewDetail:
    expense = Despesa.from_raw(
        {
            "nomeParlamentar": "Deputada Sintética",
            "siglaUF": "SP",
            "numeroSubCota": 3,
            "valorDocumento": "10.01",
            "valorGlosa": "0.00",
            "valorLiquido": "10.01",
            "ano": 2026,
            "mes": 1,
            "numeroDeputadoID": 101,
            "idDocumento": 900001,
        }
    )
    finding = Finding(
        "R08",
        "1",
        900001,
        "alta",
        Decimal("10.01"),
        {"sintetico": True},
        Provenance(
            "original-run",
            "original-step",
            SourceOrigin("original-source", "Ano-2026.json", "a" * 64, "b" * 64),
            "original-commit",
            NOW,
        ),
    )
    record = FindingExportRecord(
        finding,
        101,
        "Deputada Sintética",
        "SP",
        None,
        2026,
        1,
        None,
        None,
        None,
        None,
        Decimal("10.01"),
        Decimal("0.00"),
        Decimal("10.01"),
        None,
        1,
        100,
    )
    return ReviewDetail(record, None, expense, (), (), None)


class Repository:
    def __init__(self, factory: RunFactory) -> None:
        self.factory = factory
        self.current: ReviewDetail | None = detail()
        self.appended: list[Decision] = []
        self.reads: list[str] = []
        self.filters: list[ReviewFilters] = []
        self.failure: Exception | None = None

    def list_pending(self, filters: ReviewFilters) -> tuple[ReviewItem, ...]:
        self.filters.append(filters)
        if self.current is None:
            return ()
        return (ReviewItem(self.current.record, self.current.current_decision),)

    def get_finding(self, finding_key: str) -> ReviewDetail | None:
        self.reads.append(finding_key)
        if (
            self.current is not None
            and self.current.record.finding.finding_key == finding_key
        ):
            return self.current
        return None

    def append_decision(self, decision: Decision) -> None:
        run = self.factory.runs[-1]
        assert run.status == "running"
        assert run.run_id == decision.run_id
        assert run.require_step() == decision.step_id
        if self.failure is not None:
            raise self.failure
        assert self.current is not None
        self.appended.append(decision)
        self.current = replace(
            self.current,
            current_decision=decision,
            decisions=(*self.current.decisions, decision),
        )


def service(
    tmp_path: Path, *, reviewer: str = "Revisora Sintética"
) -> tuple[ReviewService, Repository, RunFactory]:
    factory = RunFactory(tmp_path)
    repository = Repository(factory)
    return ReviewService(repository, factory, reviewer=reviewer), repository, factory


def successful_decision() -> Decision:
    return Decision(
        "decision-id",
        detail().record.finding.finding_key,
        DecisionValue("aprovado"),
        "Revisora Sintética",
        "Comentário\ncom acentos.",
        NOW,
        "run-id",
        "step-id",
    )


def test_decision_is_immutable_and_preserves_human_comment() -> None:
    decision = successful_decision()
    assert decision.comment == "Comentário\ncom acentos."
    assert replace(decision, comment="").comment == ""
    assert replace(decision, decision=cast(DecisionValue, "revisar")).decision is (
        DecisionValue("revisar")
    )
    assert decision.decided_at == NOW
    with pytest.raises(FrozenInstanceError):
        setattr(decision, "comment", "substituição")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision_id", ""),
        ("finding_key", " "),
        ("reviewer", "\n\t"),
        ("run_id", ""),
        ("step_id", " "),
        ("decision", "unknown"),
        ("comment", None),
        ("decided_at", datetime(2026, 1, 1)),
    ],
)
def test_domain_rejects_invalid_decision_evidence(field: str, value: object) -> None:
    values = asdict(successful_decision())
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        Decision(**values)


def test_two_decisions_append_in_distinct_single_step_runs(tmp_path: Path) -> None:
    review, repository, factory = service(tmp_path)
    original = detail().record.finding
    first = review.decide(original.finding_key, "aprovado", "Documento conferido.")
    second = review.decide(
        original.finding_key, DecisionValue("rejeitado"), "Parecer humano revisto."
    )
    assert repository.appended == [first, second]
    assert first.decision == DecisionValue("aprovado")
    assert first.comment == "Documento conferido."
    assert first.decision_id != second.decision_id
    assert first.run_id != second.run_id and first.step_id != second.step_id
    assert first.reviewer == second.reviewer == "Revisora Sintética"
    assert first.decided_at == second.decided_at == NOW
    current = review.get_finding(original.finding_key)
    assert current.decisions == (first, second)
    assert current.current_decision == second
    assert current.record.finding == original
    assert len(factory.runs) == 2
    for decision, run in zip(repository.appended, factory.runs, strict=True):
        persisted = factory.ledger.get_run(run.run_id)
        assert persisted is not None and persisted.status == "completed"
        assert persisted.command == "review.decide"
        assert persisted.counters["decisions_saved"] == 1
        assert "comment" not in persisted.arguments
        steps = factory.ledger.get_steps(run.run_id)
        assert len(steps) == 1
        assert steps[0].name == "registrar_decisao"
        assert steps[0].status == "completed"
        assert steps[0].step_id == decision.step_id
        saved = [
            event
            for event in factory.ledger.get_events(run.run_id)
            if event.kind == "decision.recorded"
        ]
        assert len(saved) == 1 and saved[0].step_id == decision.step_id
        assert saved[0].attributes == {
            "decision_id": decision.decision_id,
            "finding_key": decision.finding_key,
            "decision": decision.decision.value,
            "reviewer": decision.reviewer,
        }


@pytest.mark.parametrize("value", ["aprovado", "rejeitado", "revisar"])
def test_all_human_decision_values_are_preserved(tmp_path: Path, value: str) -> None:
    review, repository, factory = service(tmp_path)
    decision = review.decide(detail().record.finding.finding_key, value)
    assert decision.decision == DecisionValue(value)
    assert decision.comment == ""
    assert repository.appended == [decision]
    assert factory.runs[0].status == "completed"


@pytest.mark.parametrize("invalid", ["decision", "reviewer", "key", "comment"])
def test_invalid_requests_are_failed_inside_a_durable_run(
    tmp_path: Path,
    invalid: str,
) -> None:
    review, repository, factory = service(
        tmp_path, reviewer="  " if invalid == "reviewer" else "Revisora"
    )
    key = " " if invalid == "key" else detail().record.finding.finding_key
    value = "not-a-decision" if invalid == "decision" else "aprovado"
    comment = cast(str, None) if invalid == "comment" else "Comentário"
    with pytest.raises((TypeError, ValueError)):
        review.decide(key, value, comment)
    assert repository.appended == []
    assert_failed_run(factory)


def assert_failed_run(factory: RunFactory) -> None:
    assert len(factory.runs) == 1
    run = factory.runs[0]
    persisted = factory.ledger.get_run(run.run_id)
    assert persisted is not None and persisted.status == "completed_with_errors"
    assert persisted.finished_at is not None
    assert persisted.counters["decisions_saved"] == 0
    steps = factory.ledger.get_steps(run.run_id)
    assert len(steps) == 1 and steps[0].status == "failed"
    events = factory.ledger.get_events(run.run_id)
    failures = [event for event in events if event.kind == "step.failed"]
    assert len(failures) == 1 and failures[0].attributes["error"]
    assert not any(event.kind == "decision.recorded" for event in events)


def test_missing_finding_failure_is_recorded_without_publication(
    tmp_path: Path,
) -> None:
    review, repository, factory = service(tmp_path)
    with pytest.raises(LookupError):
        review.decide("f" * 64, "aprovado")
    assert repository.appended == []
    assert_failed_run(factory)


def test_superseded_finding_cannot_receive_a_new_human_decision(tmp_path: Path) -> None:
    review, repository, factory = service(tmp_path)
    assert repository.current is not None
    source = repository.current.record
    repository.current = replace(
        repository.current,
        record=replace(source, finding=replace(source.finding, status="superseded")),
    )
    with pytest.raises(DomainError):
        review.decide(source.finding.finding_key, "aprovado")
    assert repository.appended == []
    assert_failed_run(factory)


def test_repository_failure_is_rethrown_after_its_run_has_finished(
    tmp_path: Path,
) -> None:
    review, repository, factory = service(tmp_path)
    failure = OSError("  armazenamento\nindisponível  ")
    repository.failure = failure
    with pytest.raises(OSError) as caught:
        review.decide(detail().record.finding.finding_key, "aprovado")
    assert caught.value is failure
    assert repository.appended == []
    assert_failed_run(factory)
    events = factory.ledger.get_events(factory.runs[0].run_id)
    event = next(event for event in events if event.kind == "step.failed")
    assert event.attributes["error"] == {
        "type": "OSError",
        "message": "armazenamento indisponível",
    }


def test_list_and_detail_reads_forward_filters_without_creating_runs(
    tmp_path: Path,
) -> None:
    review, repository, factory = service(tmp_path)
    filters = ReviewFilters(
        rule_id="R08",
        deputy_id=101,
        uf="SP",
        min_amount=Decimal("10.01"),
        limit=5,
        offset=2,
    )
    selected = review.list_pending(filters)
    assert repository.filters == [filters]
    assert repository.current is not None
    assert selected == (ReviewItem(repository.current.record, None),)
    key = repository.current.record.finding.finding_key
    assert review.get_finding(key) == repository.current
    with pytest.raises(LookupError):
        review.get_finding("f" * 64)
    review.list_pending()
    assert repository.filters[-1] == ReviewFilters()
    assert factory.runs == []
    assert not (tmp_path / "runs").exists()


def test_service_retains_revisar_items_in_the_pending_queue(tmp_path: Path) -> None:
    review, repository, factory = service(tmp_path)
    assert repository.current is not None
    pending = replace(successful_decision(), decision=DecisionValue("revisar"))
    repository.current = replace(repository.current, current_decision=pending)
    assert review.list_pending() == (ReviewItem(repository.current.record, pending),)
    assert factory.runs == []


def test_review_filters_preserve_exact_amount_and_normalize_uf() -> None:
    filters = ReviewFilters(uf="sp", min_amount=Decimal("90071992547409.91"))
    assert filters.uf == "SP"
    assert filters.min_amount == Decimal("90071992547409.91")
    assert ReviewFilters(deputy_id=0).deputy_id == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rule_id", "R8"),
        ("deputy_id", True),
        ("uf", "SÃO"),
        ("min_amount", Decimal("-0.01")),
        ("min_amount", Decimal("NaN")),
        ("min_amount", Decimal("Infinity")),
        ("min_amount", 1.5),
        ("limit", 0),
        ("limit", 1001),
        ("limit", True),
        ("offset", -1),
        ("offset", True),
    ],
)
def test_invalid_review_filters_are_rejected(field: str, value: object) -> None:
    values = asdict(ReviewFilters())
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        ReviewFilters(**values)

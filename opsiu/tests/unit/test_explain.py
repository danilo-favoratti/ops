"""Call budgets and private failure isolation in annotation orchestration."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application import explain as module
from opsiu.application.ports import (
    AnnotationKind,
    AnnotationRequest,
    AnnotationResult,
    AnnotationSpec,
    FindingExportReader,
    FindingExportRecord,
    FindingSample,
    PdfContent,
    PdfExtraction,
    PdfRepository,
    RunReader,
)
from opsiu.application.runs import Run
from opsiu.domain.findings import Finding, Provenance, SourceOrigin

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def record(document: int, rule: str = "R01") -> FindingExportRecord:
    return FindingExportRecord(
        Finding(
            rule,
            "1",
            document,
            "alta",
            Decimal("10.00"),
            {},
            Provenance(
                "original-run",
                "original-step",
                SourceOrigin("source", "input.json", "a" * 64, "b" * 64),
                "commit",
                NOW,
            ),
        ),
        101,
        "Deputada Sintética",
        "DF",
        None,
        2026,
        1,
        "Fornecedor",
        "12345678000190",
        "0001",
        NOW.date(),
        Decimal("10.00"),
        Decimal("0.00"),
        Decimal("10.00"),
        f"https://example.invalid/{document}.pdf",
        document,
        100,
    )


class Pdfs:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.lookups: list[tuple[int, str]] = []

    def latest(self, document: int, url: str) -> PdfExtraction | None:
        self.lookups.append((document, url))
        if self.text is None:
            return None
        return PdfExtraction(
            f"extraction-{document}",
            PdfContent(
                document,
                url,
                "snapshot",
                "c" * 64,
                Path("unused.pdf"),
                "d" * 64,
                10,
                "pypdf",
                "1",
                self.text,
                "e" * 64,
                not bool(self.text.strip()),
                1,
            ),
            NOW,
            "pdf-run",
            "pdf-step",
        )


class Repository:
    def __init__(self, cached: bool = False) -> None:
        self.cached = cached
        self.saved: list[tuple[str, str]] = []

    def has_success(self, finding_key: str, spec: AnnotationSpec) -> bool:
        return self.cached

    def save(
        self,
        finding_key: str,
        spec: AnnotationSpec,
        result: AnnotationResult,
        *,
        parecer_id: str,
        created_at: datetime,
        run_id: str,
        step_id: str,
    ) -> None:
        assert result.output is not None and result.failure is None
        self.saved.append((finding_key, spec.kind))


class Gateway:
    def __init__(self, *, fail: bool = False, throw: bool = False) -> None:
        self.fail = fail
        self.throw = throw
        self.calls: list[tuple[AnnotationRequest, AnnotationKind]] = []

    def spec(self, kind: AnnotationKind, rule_id: str) -> AnnotationSpec:
        return AnnotationSpec(
            kind, f"{kind}-{rule_id}", "scripted", kind, "1", "f" * 64
        )

    async def annotate(
        self, request: AnnotationRequest, *, kind: AnnotationKind
    ) -> AnnotationResult:
        self.calls.append((request, kind))
        if self.throw:
            raise ValueError("PRIVATE_PROVIDER_RESPONSE")
        if self.fail and kind == "parecer":
            return AnnotationResult("trace", None, {"message": "PRIVATE_OUTPUT"})
        return AnnotationResult("trace", {"simulado": True}, None)


def execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    gateway: Gateway,
    pdfs: Pdfs,
    repository: Repository,
    budget: int,
    force: bool = False,
) -> Run:
    monkeypatch.setattr(
        module,
        "sample_findings",
        lambda *_args, **_kwargs: FindingSample(
            "latest-check", (record(1), record(2, "R02"))
        ),
    )
    run = Run(
        ledger=JsonlLedger(tmp_path / "runs"),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="explain",
        arguments={},
        metadata=collect_metadata("settings", tmp_path),
    )
    with run:
        asyncio.run(
            module.explain(
                run,
                cast(RunReader, object()),
                cast(FindingExportReader, object()),
                cast(PdfRepository, pdfs),
                repository,
                gateway,
                rule_texts={"R01": ("1", "Regra fornecida.")},
                selected_rules=("R01",),
                per_rule=20,
                max_calls=budget,
                force=force,
            )
        )
    return run


def test_failed_explanation_consumes_budget_and_does_not_prevent_invoice_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = Gateway(fail=True)
    repository = Repository()
    pdfs = Pdfs("Texto da nota.")
    run = execute(
        tmp_path,
        monkeypatch,
        gateway=gateway,
        pdfs=pdfs,
        repository=repository,
        budget=2,
    )
    assert run.status == "completed_with_errors"
    assert [kind for _, kind in gateway.calls] == ["parecer", "nota_fiscal"]
    assert run.counters["annotation_calls"] == 2
    assert run.counters["annotations_failed"] == 1
    assert repository.saved == [(record(1).finding.finding_key, "nota_fiscal")]
    assert pdfs.lookups == [(1, "https://example.invalid/1.pdf")]
    assert run.metadata["sample"] == {
        "check_run_id": "latest-check",
        "per_rule": 20,
        "rules": ["R01"],
        "finding_keys": [record(1).finding.finding_key],
    }
    events = (tmp_path / "runs" / run.run_id / "events.jsonl").read_text()
    assert "PRIVATE_OUTPUT" not in events
    context = next(
        json.loads(line)
        for line in events.splitlines()
        if json.loads(line)["kind"] == "annotation.context"
    )
    assert context["attributes"]["pdf"]["extraction_id"] == "extraction-1"


@pytest.mark.parametrize("budget", [0, 1])
def test_exhausted_budget_stops_before_the_next_required_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int
) -> None:
    gateway = Gateway(fail=True)
    run = execute(
        tmp_path,
        monkeypatch,
        gateway=gateway,
        pdfs=Pdfs("Texto"),
        repository=Repository(),
        budget=budget,
    )
    assert len(gateway.calls) == budget
    assert run.counters["budget_exhausted"] == 1
    events = (tmp_path / "runs" / run.run_id / "events.jsonl").read_text()
    assert events.count('"kind":"budget.exhausted"') == 1


@pytest.mark.parametrize("force", [False, True])
def test_force_controls_cached_annotations_and_blank_pdf_never_calls_invoice_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force: bool
) -> None:
    gateway = Gateway()
    run = execute(
        tmp_path,
        monkeypatch,
        gateway=gateway,
        pdfs=Pdfs(" \n"),
        repository=Repository(cached=True),
        budget=3,
        force=force,
    )
    assert run.status == "completed"
    assert [kind for _, kind in gateway.calls] == (["parecer"] if force else [])


def test_gateway_exception_is_counted_without_publishing_private_error_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = Gateway(throw=True)
    repository = Repository()
    run = execute(
        tmp_path,
        monkeypatch,
        gateway=gateway,
        pdfs=Pdfs(None),
        repository=repository,
        budget=2,
    )
    assert run.status == "completed_with_errors"
    assert run.counters["annotation_calls"] == run.counters["annotations_failed"] == 1
    assert repository.saved == []
    assert (
        "PRIVATE_PROVIDER_RESPONSE"
        not in (tmp_path / "runs" / run.run_id / "events.jsonl").read_text()
    )

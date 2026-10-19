"""Application ingestion batches records and never logs during staging."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ingest import ingest
from opsiu.application.ports import EventRecord, ExpenseSourceMetadata
from opsiu.application.runs import Run
from opsiu.domain.models import Despesa
from opsiu.domain.policies import Adicional, Cap, CotaUF
from opsiu.domain.record_hash import record_sha256

pytestmark = pytest.mark.unit


class Source:
    def __init__(self, count: int, *, invalid_hash: bool = False) -> None:
        self.count = count
        self.invalid_hash = invalid_hash
        self.metadata: ExpenseSourceMetadata | None = None
        self.closed = False

    def __iter__(self) -> Iterator[tuple[dict[str, object], str]]:
        try:
            for document in range(self.count):
                raw: dict[str, object] = {
                    "nomeParlamentar": "Deputada Sintética",
                    "siglaUF": "DF",
                    "numeroSubCota": 3,
                    "valorDocumento": "10.00",
                    "valorGlosa": "0",
                    "valorLiquido": "10.00",
                    "mes": 1,
                    "ano": 2099,
                    "numeroDeputadoID": 1,
                    "idDocumento": document,
                }
                yield raw, "wrong" if self.invalid_hash else record_sha256(raw)
            self.metadata = ExpenseSourceMetadata("Ano-2099.json", "digest", 123)
        finally:
            self.closed = True


class Repository:
    def __init__(self) -> None:
        self.staging = False
        self.batch_sizes: list[int] = []
        self.published: list[ExpenseSourceMetadata] = []

    def current_source_sha256(self, source_year: int) -> str | None:
        return None

    def upsert_policies(
        self,
        caps: tuple[Cap, ...],
        quotas: tuple[CotaUF, ...],
        additions: tuple[Adicional, ...],
        *,
        run_id: str,
        step_id: str,
    ) -> None:
        assert len(caps) == 6 and len(quotas) == 27 and len(additions) == 14

    @contextmanager
    def stage(
        self, *, source_file_id: str, source_year: int, run_id: str, step_id: str
    ) -> Iterator[Repository]:
        self.staging = True
        try:
            yield self
        finally:
            self.staging = False

    def add_batch(self, expenses: tuple[Despesa, ...]) -> None:
        assert self.staging
        self.batch_sizes.append(len(expenses))

    def publish(
        self, metadata: ExpenseSourceMetadata, *, ingested_at: datetime
    ) -> None:
        assert self.staging
        self.published.append(metadata)


def make_run(tmp_path: Path, repository: Repository) -> Run:
    class TransactionCheckingLedger(JsonlLedger):
        def emit(self, event: EventRecord) -> None:
            assert not repository.staging, "ledger writes must follow transaction exit"
            super().emit(event)

    return Run(
        ledger=TransactionCheckingLedger(tmp_path / "runs"),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="ingest",
        arguments={},
        metadata=collect_metadata("hash", tmp_path),
    )


def test_bounded_batches_publish_only_after_exhausting_source(tmp_path: Path) -> None:
    source = Source(10_001)
    repository = Repository()
    run = make_run(tmp_path, repository)
    with run:
        ingest(run, repository, repository, {2099: source})

    assert run.status == "completed"
    assert repository.batch_sizes == [10_000, 1]
    assert repository.published == [source.metadata]
    assert source.closed
    assert run.counters["records_loaded"] == 10_001
    assert run.counters["batches_loaded"] == 2
    assert run.metadata["source_file_hashes"] == {"Ano-2099.json": "digest"}


def test_hash_mismatch_closes_source_and_never_publishes(tmp_path: Path) -> None:
    source = Source(2, invalid_hash=True)
    repository = Repository()
    run = make_run(tmp_path, repository)
    with run:
        ingest(run, repository, repository, {2099: source})

    assert run.status == "completed_with_errors"
    assert source.closed
    assert repository.batch_sizes == []
    assert repository.published == []
    assert run.counters["files_failed"] == 1
    assert run.metadata["source_file_hashes"] == {}

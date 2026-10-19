"""Typed ports and records shared by run orchestration and ledger adapters."""

from collections.abc import Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

from opsiu.domain.findings import Finding, SourceOrigin
from opsiu.domain.models import Despesa
from opsiu.domain.policies import Adicional, Cap, CotaUF

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type RunStatus = Literal["running", "completed", "completed_with_errors", "aborted"]
type StepStatus = Literal["running", "completed", "failed", "skipped"]


@dataclass(frozen=True)
class RunMetadata:
    settings_hash: str
    code_commit: str
    code_dirty: bool
    python_version: str
    package_version: str


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    command: str
    arguments: dict[str, JsonValue]
    metadata: RunMetadata
    started_at: datetime
    status: RunStatus = "running"
    finished_at: datetime | None = None
    counters: dict[str, int] = field(default_factory=dict)
    extra: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True)
class StepRecord:
    step_id: str
    run_id: str
    name: str
    started_at: datetime
    status: StepStatus = "running"
    finished_at: datetime | None = None


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    run_id: str
    step_id: str | None
    kind: str
    attributes: dict[str, JsonValue]
    created_at: datetime


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self) -> str: ...


class Ledger(Protocol):
    def start_run(self, run: RunRecord) -> None: ...
    def start_step(self, step: StepRecord) -> None: ...
    def finish_step(self, step: StepRecord) -> None: ...
    def emit(self, event: EventRecord) -> None: ...
    def finish_run(self, run: RunRecord) -> None: ...


class RunReader(Protocol):
    def list_runs(self) -> tuple[RunRecord, ...]: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
    def get_steps(self, run_id: str) -> tuple[StepRecord, ...]: ...
    def get_events(self, run_id: str) -> tuple[EventRecord, ...]: ...


class PolicyRepository(Protocol):
    def upsert_policies(
        self,
        caps: tuple[Cap, ...],
        quotas: tuple[CotaUF, ...],
        additions: tuple[Adicional, ...],
        *,
        run_id: str,
        step_id: str,
    ) -> None:
        """Insert new dated values; equal existing values are unchanged.

        A different value for an existing key/date is a conflict, and the whole
        batch must roll back. A new effective date preserves existing versions.
        """
        ...


@dataclass(frozen=True)
class ExpenseSourceMetadata:
    source_file: str
    source_sha256: str
    size_bytes: int


class ExpenseSource(Protocol):
    @property
    def metadata(self) -> ExpenseSourceMetadata | None:
        """Complete-file metadata, available only after successful iteration."""
        ...

    def __iter__(self) -> Iterator[tuple[dict[str, object], str]]:
        """Yield raw records and their shared canonical SHA-256 values."""
        ...


class ExpenseStage(Protocol):
    def add_batch(self, expenses: tuple[Despesa, ...]) -> None: ...

    def publish(
        self, metadata: ExpenseSourceMetadata, *, ingested_at: datetime
    ) -> None:
        """Atomically publish the complete source and rebuild current deputies."""
        ...


class ExpenseRepository(Protocol):
    def current_source_sha256(self, source_year: int) -> str | None: ...

    def stage(
        self, *, source_file_id: str, source_year: int, run_id: str, step_id: str
    ) -> AbstractContextManager[ExpenseStage]:
        """Roll back all staged rows unless explicitly published successfully."""
        ...


@dataclass(frozen=True)
class FindingChanges:
    inserted: int = 0
    unchanged: int = 0
    superseded: int = 0


class CheckRepository(Protocol):
    def has_relation(self, name: str) -> bool: ...

    def candidates(self, sql: str) -> Iterator[Mapping[str, object]]:
        """Stream candidate rows from exactly one read-only SELECT statement."""
        ...

    def source_origin(self, id_documento: int) -> SourceOrigin: ...

    def current_sources(self) -> tuple[ExpenseSourceMetadata, ...]: ...

    def publish_findings(
        self,
        rule_id: str,
        rule_version: str,
        findings: tuple[Finding, ...],
        *,
        run_id: str,
        step_id: str,
        finished_at: datetime,
    ) -> FindingChanges:
        """Atomically publish complete results and supersede previous active rows."""
        ...


@dataclass(frozen=True)
class FindingExportRecord:
    finding: Finding
    numero_deputado_id: int
    nome_parlamentar: str
    sigla_uf: str
    sigla_partido: str | None
    ano: int
    mes: int
    fornecedor: str | None
    cnpj_cpf_digits: str | None
    numero: str | None
    data_emissao: date | None
    valor_documento: Decimal
    valor_glosa: Decimal
    valor_liquido: Decimal
    url_documento: str | None
    source_record_number: int
    source_size_bytes: int


class FindingExportReader(Protocol):
    def active_findings(self) -> Iterator[FindingExportRecord]:
        """Read active findings with their original source expense context."""
        ...


@dataclass(frozen=True)
class Artifact:
    path: str
    media_type: str
    sha256: str
    size_bytes: int


class ArtifactWriter(Protocol):
    def write_text(
        self, name: str, chunks: Iterable[str], *, media_type: str
    ) -> Artifact:
        """Create a new UTF-8 artifact exclusively inside the active run step."""
        ...

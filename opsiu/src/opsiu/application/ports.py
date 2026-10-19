"""Typed ports and records shared by run orchestration and ledger adapters."""

import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol

from opsiu.domain.decisions import Decision
from opsiu.domain.findings import Finding, SourceOrigin
from opsiu.domain.models import Despesa
from opsiu.domain.policies import Adicional, Cap, CotaUF

if TYPE_CHECKING:
    from opsiu.application.runs import Run

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
    valor_glosa: Decimal | None
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


@dataclass(frozen=True)
class SnapshotManifest:
    source: str
    snapshot_id: str
    url: str
    status: int
    headers: Mapping[str, str]
    sha256: str
    size_bytes: int
    fetched_at: datetime
    run_id: str
    step_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True)
class Snapshot:
    source: str
    snapshot_id: str
    path: Path
    manifest: SnapshotManifest


class SnapshotStore(Protocol):
    def download(self, source: str, url: str) -> Snapshot:
        """Publish a new immutable snapshot inside an active run step."""
        ...

    def list_snapshots(self, source: str) -> tuple[Snapshot, ...]:
        """Return validated local snapshots, newest first, without HTTP."""
        ...

    def load_latest(self, source: str) -> Snapshot | None: ...

    def load(self, source: str, snapshot_id: str) -> Snapshot:
        """Validate and replay one exact local snapshot without HTTP."""
        ...


@dataclass(frozen=True)
class TseCandidate:
    candidate_id: str
    year: int
    cpf: str | None
    nome_normalizado: str
    uf: str
    cargo: str
    source_member: str
    source_record_number: int


@dataclass(frozen=True)
class TseImport:
    import_id: str
    snapshot_id: str
    source_sha256: str
    year: int
    records_count: int


class TseSource(Protocol):
    def candidates(self, snapshot: Snapshot, year: int) -> Iterator[TseCandidate]: ...


class TseStage(Protocol):
    def add_batch(self, candidates: tuple[TseCandidate, ...]) -> None: ...

    def publish(self, *, loaded_at: datetime) -> TseImport: ...


class TseRepository(Protocol):
    def current_import(self, year: int) -> TseImport | None: ...

    def stage(
        self,
        *,
        import_id: str,
        snapshot: Snapshot,
        year: int,
        run_id: str,
        step_id: str,
    ) -> AbstractContextManager[TseStage]: ...


type CnpjFamily = Literal["empresas", "socios", "estabelecimentos"]


@dataclass(frozen=True)
class CnpjSnapshotFile:
    name: str
    family: CnpjFamily
    sha256: str
    size_bytes: int
    original_path: str


@dataclass(frozen=True)
class CnpjSnapshotManifest:
    snapshot_id: str
    source_directory: str
    sha256: str
    size_bytes: int
    captured_at: datetime
    run_id: str
    step_id: str
    files: tuple[CnpjSnapshotFile, ...]


@dataclass(frozen=True)
class CnpjSnapshot:
    snapshot_id: str
    path: Path
    manifest: CnpjSnapshotManifest


class CnpjSnapshotStore(Protocol):
    def capture(self, directory: Path) -> CnpjSnapshot: ...
    def load(self, snapshot_id: str) -> CnpjSnapshot: ...
    def load_latest(self) -> CnpjSnapshot | None: ...
    def list_snapshots(self) -> tuple[CnpjSnapshot, ...]: ...


@dataclass(frozen=True)
class CnpjOrigin:
    source_file: str
    source_sha256: str
    source_member: str
    source_record_number: int


@dataclass(frozen=True)
class CnpjCompany:
    cnpj_root: str
    razao_social: str
    natureza_juridica: str
    origin: CnpjOrigin


@dataclass(frozen=True)
class CnpjPartner:
    cnpj_root: str
    tipo_socio: int
    nome_socio: str
    qualificacao: str
    origin: CnpjOrigin


@dataclass(frozen=True)
class CnpjEstablishment:
    cnpj_root: str
    cnpj: str
    cnae_principal: str | None
    origin: CnpjOrigin


type CnpjRecord = CnpjCompany | CnpjPartner | CnpjEstablishment


@dataclass(frozen=True)
class CnpjProgress:
    family: CnpjFamily
    source_file: str
    source_member: str
    scanned_rows: int


@dataclass(frozen=True)
class CnpjImport:
    import_id: str
    snapshot_id: str
    source_sha256: str
    roots_sha256: str
    companies: int
    partners: int
    establishments: int


class CnpjSource(Protocol):
    def records(
        self, snapshot: CnpjSnapshot, roots: frozenset[str]
    ) -> Iterator[CnpjRecord | CnpjProgress]: ...


class CnpjStage(Protocol):
    def add_batch(self, records: tuple[CnpjRecord, ...]) -> None: ...
    def publish(self, *, loaded_at: datetime) -> CnpjImport: ...


class CnpjRepository(Protocol):
    def requested_roots(self) -> frozenset[str]: ...
    def current_import(self) -> CnpjImport | None: ...
    def canonical_snapshot_id(self, sha256: str) -> str | None: ...
    def stage(
        self,
        *,
        import_id: str,
        snapshot: CnpjSnapshot,
        roots: frozenset[str],
        roots_sha256: str,
        run_id: str,
        step_id: str,
    ) -> AbstractContextManager[CnpjStage]: ...


@dataclass(frozen=True)
class FindingSample:
    check_run_id: str
    records: tuple[FindingExportRecord, ...]


@dataclass(frozen=True)
class PdfContent:
    id_documento: int
    url: str
    snapshot_id: str
    snapshot_sha256: str
    path: Path
    sha256: str
    size_bytes: int
    extractor: str
    extractor_version: str
    text: str
    text_sha256: str
    needs_ocr: bool
    page_count: int


@dataclass(frozen=True)
class PdfExtraction:
    extraction_id: str
    content: PdfContent
    created_at: datetime
    run_id: str
    step_id: str


class PdfSource(Protocol):
    def prepare(
        self,
        id_documento: int,
        url: str,
        *,
        previous: PdfExtraction | None = None,
        refresh: bool = False,
        initial_path_reserved: bool = False,
    ) -> PdfContent: ...


class PdfRepository(Protocol):
    def has_document(self, id_documento: int) -> bool:
        """Whether any immutable extraction reserves this document's initial path."""
        ...

    def latest(self, id_documento: int, url: str) -> PdfExtraction | None: ...

    def publish(
        self,
        content: PdfContent,
        finding_keys: tuple[str, ...],
        *,
        extraction_id: str,
        created_at: datetime,
        run_id: str,
        step_id: str,
    ) -> PdfExtraction: ...


type AnnotationKind = Literal["parecer", "nota_fiscal"]


@dataclass(frozen=True)
class AnnotationSpec:
    kind: AnnotationKind
    agent_id: str
    model: str
    prompt_name: str
    prompt_version: str
    prompt_sha256: str


@dataclass(frozen=True)
class AnnotationRequest:
    record: FindingExportRecord
    rule_text: str
    pdf: PdfExtraction | None


@dataclass(frozen=True)
class AnnotationResult:
    trace_id: str
    output: dict[str, JsonValue] | None
    failure: dict[str, JsonValue] | None


class AgentGateway(Protocol):
    def spec(self, kind: AnnotationKind, rule_id: str) -> AnnotationSpec: ...

    async def annotate(
        self, request: AnnotationRequest, *, kind: AnnotationKind
    ) -> AnnotationResult: ...


class AnnotationRepository(Protocol):
    def has_success(self, finding_key: str, spec: AnnotationSpec) -> bool: ...

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
    ) -> None: ...


@dataclass(frozen=True)
class SummaryFinding:
    finding: Finding
    numero_deputado_id: int
    nome_parlamentar: str
    ano: int
    mes: int
    parecer_id: str
    parecer_trace_id: str
    parecer: dict[str, JsonValue]


@dataclass(frozen=True)
class DeputyReviewInput:
    numero_deputado_id: int
    nome_parlamentar: str
    findings: tuple[SummaryFinding, ...]


@dataclass(frozen=True)
class SummarySpec:
    model: str
    prompt_name: str
    prompt_version: str
    prompt_sha256: str


class DeputyReviewer(Protocol):
    def spec(self) -> SummarySpec: ...

    async def summarize(self, request: DeputyReviewInput) -> AnnotationResult: ...


class SummaryRepository(Protocol):
    def inputs(
        self, model: str, *, deputy_id: int | None = None
    ) -> Iterator[DeputyReviewInput]: ...

    def has_success(
        self, deputy_id: int, input_sha256: str, spec: SummarySpec
    ) -> bool: ...

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
    ) -> None: ...


@dataclass(frozen=True)
class ReviewFilters:
    rule_id: str | None = None
    deputy_id: int | None = None
    uf: str | None = None
    min_amount: Decimal | None = None
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        if self.rule_id is not None and (
            not isinstance(self.rule_id, str)
            or re.fullmatch(r"R[0-9]{2}", self.rule_id) is None
        ):
            raise ValueError("O identificador da regra deve seguir o formato R01.")
        if self.deputy_id is not None and type(self.deputy_id) is not int:
            raise ValueError("O identificador do deputado deve ser inteiro.")
        if self.uf is not None:
            if (
                not isinstance(self.uf, str)
                or re.fullmatch(r"[A-Za-z]{2}", self.uf) is None
            ):
                raise ValueError("A UF deve conter duas letras.")
            object.__setattr__(self, "uf", self.uf.upper())
        if self.min_amount is not None and (
            not isinstance(self.min_amount, Decimal)
            or not self.min_amount.is_finite()
            or self.min_amount < 0
        ):
            raise ValueError("O valor mínimo deve ser decimal, finito e não negativo.")
        if type(self.limit) is not int or not 1 <= self.limit <= 1_000:
            raise ValueError("O limite deve ser um inteiro entre 1 e 1000.")
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("O deslocamento deve ser um inteiro não negativo.")


@dataclass(frozen=True)
class ReviewItem:
    record: FindingExportRecord
    current_decision: Decision | None


@dataclass(frozen=True)
class ReviewAnnotation:
    parecer_id: str
    finding_key: str
    agent_trace_id: str
    agent_id: str
    kind: AnnotationKind
    model: str
    prompt_version: str
    output: dict[str, JsonValue]
    created_at: datetime
    run_id: str
    step_id: str


@dataclass(frozen=True)
class ReviewDetail:
    record: FindingExportRecord
    current_decision: Decision | None
    expense: Despesa
    decisions: tuple[Decision, ...]
    annotations: tuple[ReviewAnnotation, ...]
    pdf: PdfExtraction | None


class RunFactory(Protocol):
    def __call__(
        self, command: str, arguments: dict[str, JsonValue]
    ) -> AbstractContextManager["Run"]: ...


class ReviewRepository(Protocol):
    def list_pending(self, filters: ReviewFilters) -> tuple[ReviewItem, ...]: ...

    def get_finding(self, finding_key: str) -> ReviewDetail | None: ...

    def append_decision(self, decision: Decision) -> None: ...


@dataclass(frozen=True)
class FinalReportRecord:
    record: FindingExportRecord
    decision: Decision
    pareceres: tuple[ReviewAnnotation, ...]


class FinalReportReader(Protocol):
    def approved_findings(self) -> Iterator[FinalReportRecord]:
        """Stream one consistent snapshot, ordered by original deputy and finding."""
        ...

"""Request-local connections open only after a durable run and step exist."""

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager

from duckdb import DuckDBPyConnection

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.review_repository import DuckDBReviewRepository
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ports import JsonValue, ReviewDetail, ReviewFilters, ReviewItem
from opsiu.application.runs import Run
from opsiu.domain.decisions import Decision
from opsiu.settings import Settings


class RequestScope:
    def __init__(self, settings: Settings, *, preflight: Callable[[], None]) -> None:
        self.settings = settings
        self.preflight = preflight
        self.run: Run | None = None
        self.resources: ExitStack | None = None
        self.database_ledger: DuckDBLedger | None = None
        self.connection: DuckDBPyConnection | None = None
        self.repository = LazyReviewRepository(self)

    @contextmanager
    def run_factory(
        self, command: str, arguments: dict[str, JsonValue]
    ) -> Iterator[Run]:
        if self.run is not None:
            raise RuntimeError("a request owns exactly one run")
        files = JsonlLedger(self.settings.paths.runs_dir)
        ledger = DuckDBLedger()
        self.database_ledger = ledger
        run = Run(
            ledger=CompositeLedger(files, ledger),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command=command,
            arguments=arguments,
            metadata=collect_metadata(self.settings.settings_hash()),
        )
        self.run = run
        with ExitStack() as resources:
            self.resources = resources
            try:
                with run:
                    yield run
            finally:
                self.resources = None
                self.connection = None

    def database(self) -> DuckDBPyConnection:
        if self.run is None or self.resources is None or self.database_ledger is None:
            raise RuntimeError("database access requires an active request run")
        self.run.require_step()
        if self.connection is None:
            try:
                self.preflight()
            except Exception:
                # Rejected forms still join an existing audit ledger. They never
                # initialize a database or migrate/query review data.
                if self.settings.paths.db_path.exists():
                    try:
                        existing = self.resources.enter_context(
                            connect_database(self.settings.paths.db_path)
                        )
                        if DuckDBLedger.schema_exists(existing):
                            self.database_ledger.attach(existing)
                    except Exception as audit_error:
                        self.run.emit(
                            "review.audit_database_unavailable",
                            {
                                "error_type": type(audit_error).__name__,
                            },
                        )
                raise
            connection = self.resources.enter_context(
                connect_database(self.settings.paths.db_path)
            )
            self.connection = connection
            try:
                migrations = apply_migrations(connection)
            finally:
                if DuckDBLedger.schema_exists(connection):
                    self.database_ledger.attach(connection)
            self.run.counters["migrations_applied"] = len(migrations)
            self.run.emit("db.migrated", {"migrations": list(migrations)})
        return self.connection


class LazyReviewRepository:
    def __init__(self, scope: RequestScope) -> None:
        self.scope = scope
        self.last_detail: ReviewDetail | None = None

    def list_pending(self, filters: ReviewFilters) -> tuple[ReviewItem, ...]:
        return DuckDBReviewRepository(self.scope.database()).list_pending(filters)

    def count_pending(self, filters: ReviewFilters) -> int:
        return DuckDBReviewRepository(self.scope.database()).count_pending(filters)

    def get_finding(self, finding_key: str) -> ReviewDetail | None:
        self.last_detail = DuckDBReviewRepository(self.scope.database()).get_finding(
            finding_key
        )
        return self.last_detail

    def append_decision(self, decision: Decision) -> None:
        repository = DuckDBReviewRepository(self.scope.database())
        repository.append_decision(decision)
        # Refresh the HTMX panel while still inside the decision's sole step.
        self.last_detail = repository.get_finding(decision.finding_key)

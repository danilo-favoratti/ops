"""Ingestion orchestration over typed sources and persistence ports."""

from collections.abc import Mapping

from opsiu.application.ports import ExpenseRepository, ExpenseSource, PolicyRepository
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError
from opsiu.domain.models import Despesa
from opsiu.domain.policies import ADICIONAIS, CAPS, COTAS_UF


def load_policies(run: Run, repository: PolicyRepository) -> None:
    """Load the published dated policy rows inside the caller's active step."""
    step_id = run.require_step()
    repository.upsert_policies(
        CAPS, COTAS_UF, ADICIONAIS, run_id=run.run_id, step_id=step_id
    )
    counts = {
        "policy_caps": len(CAPS),
        "policy_quotas": len(COTAS_UF),
        "policy_additions": len(ADICIONAIS),
    }
    run.counters.update(counts)
    run.emit("policies.loaded", {key: value for key, value in counts.items()})


BATCH_SIZE = 10_000


def ingest(
    run: Run,
    policies: PolicyRepository,
    expenses: ExpenseRepository,
    sources: Mapping[int, ExpenseSource],
    *,
    force: bool = False,
) -> None:
    """Load reference policies and isolate each complete yearly source in a step."""
    with run.step("carregar_politicas") as policy_step:
        load_policies(run, policies)
    if policy_step.record.status == "failed":
        return
    for name in (
        "files_loaded",
        "files_skipped",
        "files_failed",
        "records_loaded",
        "batches_loaded",
    ):
        run.counters.setdefault(name, 0)
    for year, source in sources.items():
        with run.step(f"ingerir_{year}") as step:
            source_file_id = run.ids.new_id()
            count = batches = 0
            with expenses.stage(
                source_file_id=source_file_id,
                source_year=year,
                run_id=run.run_id,
                step_id=run.require_step(),
            ) as stage:
                batch: list[Despesa] = []
                iterator = iter(source)
                try:
                    for raw, expected_hash in iterator:
                        expense = Despesa.from_raw(raw)
                        if expense.record_sha256 != expected_hash:
                            raise DomainError("source record hash mismatch")
                        if expense.ano != year:
                            raise DomainError("expense year differs from source year")
                        batch.append(expense)
                        count += 1
                        if len(batch) == BATCH_SIZE:
                            stage.add_batch(tuple(batch))
                            batches += 1
                            batch.clear()
                    if batch:
                        stage.add_batch(tuple(batch))
                        batches += 1
                finally:
                    close = getattr(iterator, "close", None)
                    if callable(close):
                        close()
                metadata = source.metadata
                if metadata is None:
                    raise ValueError("source did not provide complete-file metadata")
                unchanged = (
                    not force
                    and expenses.current_source_sha256(year) == metadata.source_sha256
                )
                if not unchanged:
                    stage.publish(metadata, ingested_at=run.clock.now())
            # The transaction has committed or rolled back before any ledger write.
            hashes = run.metadata["source_file_hashes"]
            assert isinstance(hashes, dict)
            hashes[metadata.source_file] = metadata.source_sha256
            if unchanged:
                run.counters["files_skipped"] += 1
                step.skip("Arquivo inalterado; SHA-256 igual ao da versão atual.")
                run.emit(
                    "source.skipped",
                    {
                        "year": year,
                        "path": metadata.source_file,
                        "sha256": metadata.source_sha256,
                    },
                )
            else:
                run.counters["files_loaded"] += 1
                run.counters["records_loaded"] += count
                run.counters["batches_loaded"] += batches
                run.emit(
                    "source.loaded",
                    {
                        "source_file_id": source_file_id,
                        "year": year,
                        "path": metadata.source_file,
                        "sha256": metadata.source_sha256,
                        "size_bytes": metadata.size_bytes,
                        "records": count,
                        "batches": batches,
                        "forced": force,
                    },
                )
        if step.record.status == "failed":
            run.counters["files_failed"] += 1

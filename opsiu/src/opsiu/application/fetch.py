"""Run-scoped acquisition and atomic normalization of external snapshots."""

from opsiu.application.ports import (
    Snapshot,
    SnapshotStore,
    TseCandidate,
    TseRepository,
    TseSource,
)
from opsiu.application.runs import ConfigurationError, Run
from opsiu.domain.policies import ELECTION_DATES


def fetch_tse(
    run: Run,
    store: SnapshotStore,
    source: TseSource,
    repository: TseRepository,
    *,
    year: int,
    url: str,
    snapshot_id: str | None = None,
    replay: bool = False,
) -> None:
    if year not in ELECTION_DATES:
        raise ConfigurationError("O ano eleitoral deve ser 2024 ou 2026.")
    if snapshot_id is not None and replay:
        raise ConfigurationError("Use apenas --snapshot-id ou --replay.")
    name = f"tse_{year}"
    snapshot: Snapshot | None = None
    with run.step(f"obter_tse_{year}") as acquisition:
        if snapshot_id is not None:
            snapshot = store.load(name, snapshot_id)
        elif replay:
            snapshot = store.load_latest(name)
            if snapshot is None:
                raise LookupError(f"Nenhum snapshot local disponível para {name}.")
        else:
            snapshot = store.download(name, url)
        run.emit(
            "tse.snapshot_selected",
            {
                "year": year,
                "snapshot_id": snapshot.snapshot_id,
                "sha256": snapshot.manifest.sha256,
                "replay": replay or snapshot_id is not None,
            },
        )
    if acquisition.record.status == "failed":
        return
    assert snapshot is not None
    with run.step(f"carregar_tse_{year}") as loading:
        current = repository.current_import(year)
        if current is not None and current.source_sha256 == snapshot.manifest.sha256:
            run.counters["tse_imports_skipped"] = 1
            run.emit(
                "tse.unchanged",
                {"year": year, "snapshot_id": current.snapshot_id},
            )
            loading.skip("O conteúdo já é a versão normalizada atual deste ano.")
            return
        iterator = source.candidates(snapshot, year)
        try:
            with repository.stage(
                import_id=run.ids.new_id(),
                snapshot=snapshot,
                year=year,
                run_id=run.run_id,
                step_id=run.require_step(),
            ) as stage:
                batch: list[TseCandidate] = []
                for candidate in iterator:
                    batch.append(candidate)
                    if len(batch) == 10_000:
                        stage.add_batch(tuple(batch))
                        batch.clear()
                if batch:
                    stage.add_batch(tuple(batch))
                imported = stage.publish(loaded_at=run.clock.now())
        finally:
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
        run.counters["tse_imports_loaded"] = 1
        run.counters["tse_candidates_loaded"] = imported.records_count
        imports = run.metadata.setdefault("tse_imports", [])
        assert isinstance(imports, list)
        imports.append(
            {
                "import_id": imported.import_id,
                "snapshot_id": imported.snapshot_id,
                "source_sha256": imported.source_sha256,
                "year": year,
            }
        )
        run.emit(
            "tse.loaded",
            {
                "year": year,
                "snapshot_id": imported.snapshot_id,
                "source_sha256": imported.source_sha256,
                "records": imported.records_count,
            },
        )

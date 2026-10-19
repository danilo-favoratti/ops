"""Exclusive, streaming artifact writes inside an active run directory."""

import hashlib
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from opsiu.application.ports import Artifact
from opsiu.application.runs import Run


class FileArtifactWriter:
    def __init__(self, runs_dir: Path, run: Run) -> None:
        self.run = run
        self.directory = runs_dir / str(UUID(run.run_id))

    def write_text(
        self, name: str, chunks: Iterable[str], *, media_type: str
    ) -> Artifact:
        self.run.require_step()
        if (
            not name
            or Path(name).name != name
            or name
            in {".", "..", "manifest.json", "events.jsonl", "log.jsonl", "report.md"}
        ):
            raise ValueError("artifact name must be a non-reserved file basename")
        if not self.directory.is_dir():
            raise RuntimeError("the run directory has not been created by its ledger")
        path = self.directory / name
        digest = hashlib.sha256()
        size = 0
        created = False
        try:
            with path.open("xb") as output:
                created = True
                for chunk in chunks:
                    self.run.require_step()
                    data = chunk.encode("utf-8")
                    output.write(data)
                    digest.update(data)
                    size += len(data)
        except BaseException:
            if created:
                path.unlink()  # An incomplete, unpublished artifact is not retained.
            raise
        return Artifact(str(path), media_type, digest.hexdigest(), size)

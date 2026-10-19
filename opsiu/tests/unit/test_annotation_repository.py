"""Annotation history requires a successful trace with matching provenance."""

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.annotation_repository import DuckDBAnnotationRepository
from opsiu.application.ports import AnnotationResult, AnnotationSpec, JsonValue
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=UTC)
FINDING = "a" * 64
OTHER_FINDING = "b" * 64
SPEC = AnnotationSpec(
    "parecer", "explicar-achado-R08", "scripted", "explicar-achado", "1", "c" * 64
)
OUTPUT: dict[str, JsonValue] = {
    "fundamento": "Indício sintético sujeito a revisão.",
    "confianca": "baixa",
    "evidencias_citadas": ["registro.cnpj_cpf"],
    "recomendacao": "revisar",
}
MIGRATION = (
    Path(__file__).parents[2] / "src/opsiu/adapters/duckdb/migrations/0007_agents.sql"
)


def identity(number: int) -> str:
    return f"00000000-0000-7000-8000-{number:012d}"


@pytest.fixture
def database() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE runs(run_id UUID PRIMARY KEY, status VARCHAR); "
            "CREATE TABLE steps(step_id UUID PRIMARY KEY, "
            "run_id UUID REFERENCES runs(run_id), status VARCHAR); "
            "CREATE TABLE findings(finding_key VARCHAR PRIMARY KEY, status VARCHAR)"
        )
        connection.execute(MIGRATION.read_text())
        connection.execute(
            "INSERT INTO runs VALUES (?, 'running'), (?, 'running')",
            [identity(1), identity(2)],
        )
        connection.execute(
            "INSERT INTO steps VALUES (?, ?, 'running'), (?, ?, 'running')",
            [identity(3), identity(1), identity(4), identity(2)],
        )
        connection.execute(
            "INSERT INTO findings VALUES (?, 'active'), (?, 'active')",
            [FINDING, OTHER_FINDING],
        )
        yield connection


def trace(
    database: DuckDBPyConnection,
    *,
    number: int = 10,
    spec: AnnotationSpec = SPEC,
    overrides: dict[str, object] | None = None,
) -> AnnotationResult:
    values: dict[str, object] = {
        "agent_trace_id": identity(number),
        "run_id": identity(1),
        "step_id": identity(3),
        "finding_key": FINDING,
        "agent_id": spec.agent_id,
        "agent_version": "1",
        "model": spec.model,
        "prompt_name": spec.prompt_name,
        "prompt_version": spec.prompt_version,
        "prompt_sha256": spec.prompt_sha256,
        "input_schema": "input",
        "output_schema": "output",
        "instructions": "Anotar apenas a evidência fornecida.",
        "input_json": "{}",
        "output_json": json.dumps(dict(reversed(tuple(OUTPUT.items())))),
        "usage_json": "{}",
        "failure_json": None,
        "status": "succeeded",
        "started_at": NOW,
        "finished_at": NOW,
        "duration_ms": 0.0,
    }
    values.update(overrides or {})
    database.execute(
        "INSERT INTO agent_traces VALUES (" + ", ".join("?" for _ in values) + ")",
        list(values.values()),
    )
    return AnnotationResult(identity(number), dict(OUTPUT), None)


def save(
    database: DuckDBPyConnection,
    result: AnnotationResult,
    *,
    number: int = 20,
    spec: AnnotationSpec = SPEC,
    finding_key: str = FINDING,
    run_id: str | None = None,
    step_id: str | None = None,
) -> None:
    DuckDBAnnotationRepository(database).save(
        finding_key,
        spec,
        result,
        parecer_id=identity(number),
        created_at=NOW,
        run_id=run_id or identity(1),
        step_id=step_id or identity(3),
    )


def count(database: DuckDBPyConnection) -> int:
    row = database.execute("SELECT count(*) FROM pareceres").fetchone()
    assert row is not None
    return int(row[0])


def test_success_matches_structural_output_and_retains_original_history(
    database: DuckDBPyConnection,
) -> None:
    repository = DuckDBAnnotationRepository(database)
    assert not repository.has_success(FINDING, SPEC)
    result = trace(database)
    save(database, result)
    original = database.execute("SELECT * FROM pareceres").fetchall()
    assert repository.has_success(FINDING, SPEC)
    assert count(database) == 1
    assert json.loads(original[0][7]) == OUTPUT
    assert database.execute("SELECT status FROM findings").fetchall() == [
        ("active",),
        ("active",),
    ]
    database.execute("UPDATE runs SET status = 'completed'")
    database.execute("UPDATE steps SET status = 'completed'")
    assert repository.has_success(FINDING, SPEC)
    assert database.execute("SELECT * FROM pareceres").fetchall() == original


@pytest.mark.parametrize(
    "spec",
    [
        replace(SPEC, agent_id="explicar-achado-R09"),
        replace(SPEC, kind="nota_fiscal"),
        replace(SPEC, model="another-model"),
        replace(SPEC, prompt_version="2"),
    ],
)
def test_success_key_distinguishes_agent_kind_model_and_version(
    database: DuckDBPyConnection,
    spec: AnnotationSpec,
) -> None:
    save(database, trace(database))
    assert not DuckDBAnnotationRepository(database).has_success(FINDING, spec)
    assert not DuckDBAnnotationRepository(database).has_success(OTHER_FINDING, SPEC)


def test_superseded_findings_are_not_successful_resume_candidates(
    database: DuckDBPyConnection,
) -> None:
    save(database, trace(database))
    database.execute(
        "UPDATE findings SET status = 'superseded' WHERE finding_key = ?", [FINDING]
    )
    assert not DuckDBAnnotationRepository(database).has_success(FINDING, SPEC)
    assert count(database) == 1


def test_new_traces_append_but_reusing_a_trace_or_annotation_id_rejects(
    database: DuckDBPyConnection,
) -> None:
    first = trace(database)
    save(database, first)
    original = database.execute("SELECT * FROM pareceres").fetchall()[0]
    second = trace(database, number=11)
    save(database, second, number=21)
    assert count(database) == 2
    assert (
        database.execute(
            "SELECT * FROM pareceres WHERE parecer_id = ?", [identity(20)]
        ).fetchone()
        == original
    )
    with pytest.raises(duckdb.ConstraintException):
        save(database, first, number=22)
    third = trace(database, number=12)
    with pytest.raises(duckdb.ConstraintException):
        save(database, third, number=20)
    assert count(database) == 2


@pytest.mark.parametrize("inactive", ["run", "step", "mismatched_step", "missing_step"])
def test_writes_require_matching_persisted_active_run_and_step(
    database: DuckDBPyConnection,
    inactive: str,
) -> None:
    result = trace(database)
    if inactive == "run":
        database.execute(
            "UPDATE runs SET status = 'completed' WHERE run_id = ?", [identity(1)]
        )
    elif inactive == "step":
        database.execute(
            "UPDATE steps SET status = 'failed' WHERE step_id = ?", [identity(3)]
        )
    step_id = {"mismatched_step": identity(4), "missing_step": identity(99)}.get(
        inactive
    )
    with pytest.raises(RuntimeError, match="active run and step"):
        save(database, result, step_id=step_id)
    assert count(database) == 0


@pytest.mark.parametrize("finding", [FINDING, "d" * 64])
def test_inactive_or_missing_findings_cannot_receive_annotations(
    database: DuckDBPyConnection,
    finding: str,
) -> None:
    result = trace(database)
    database.execute("UPDATE findings SET status = 'superseded'")
    with pytest.raises(DomainError, match="active finding"):
        save(database, result, finding_key=finding)
    assert count(database) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("finding_key", OTHER_FINDING),
        ("run_id", identity(2)),
        ("step_id", identity(4)),
        ("agent_id", "another-agent"),
        ("model", "another-model"),
        ("prompt_name", "revisar-nota"),
        ("prompt_version", "2"),
        ("prompt_sha256", "d" * 64),
        ("status", "failed"),
        ("status", "cancelled"),
        ("failure_json", '{"code":"failure"}'),
        ("output_json", None),
        ("output_json", "[]"),
        ("output_json", '{"fundamento":"changed"}'),
    ],
)
def test_trace_lineage_status_and_output_must_match(
    database: DuckDBPyConnection,
    field: str,
    value: object,
) -> None:
    result = trace(database, overrides={field: value})
    with pytest.raises(DomainError):
        save(database, result)
    assert count(database) == 0


def test_missing_trace_and_failed_results_never_write(
    database: DuckDBPyConnection,
) -> None:
    result = trace(database)
    for invalid in (
        replace(result, trace_id=identity(99)),
        replace(result, failure={"code": "failed"}),
        replace(result, output=None),
        replace(result, output={"invalid": float("nan")}),
    ):
        with pytest.raises(DomainError):
            save(database, invalid)
    assert count(database) == 0


@pytest.mark.parametrize(
    "changed",
    [replace(SPEC, prompt_sha256="d" * 64), replace(SPEC, prompt_name="changed")],
)
def test_prompt_changes_need_new_version_even_when_forcing_an_append(
    database: DuckDBPyConnection,
    changed: AnnotationSpec,
) -> None:
    save(database, trace(database))
    original = database.execute("SELECT * FROM pareceres").fetchall()
    with pytest.raises(DomainError, match="without a version change"):
        DuckDBAnnotationRepository(database).has_success(FINDING, changed)
    result = trace(database, number=11, spec=changed)
    with pytest.raises(DomainError, match="without a version change"):
        save(database, result, number=21, spec=changed)
    assert database.execute("SELECT * FROM pareceres").fetchall() == original


def test_new_prompt_version_can_append_with_a_new_hash(
    database: DuckDBPyConnection,
) -> None:
    save(database, trace(database))
    spec = replace(SPEC, prompt_version="2", prompt_sha256="d" * 64)
    save(database, trace(database, number=11, spec=spec), number=21, spec=spec)
    repository = DuckDBAnnotationRepository(database)
    assert repository.has_success(FINDING, SPEC)
    assert repository.has_success(FINDING, spec)
    assert count(database) == 2


def test_resume_rejects_annotation_output_that_disagrees_with_its_private_trace(
    database: DuckDBPyConnection,
) -> None:
    save(database, trace(database))
    database.execute("UPDATE pareceres SET output_json = '{}' ")
    with pytest.raises(DomainError, match="conflicts with its trace"):
        DuckDBAnnotationRepository(database).has_success(FINDING, SPEC)

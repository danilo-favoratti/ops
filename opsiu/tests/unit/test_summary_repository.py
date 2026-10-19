"""Deputy summaries retain exact original findings, opinions, and private traces."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import duckdb
import pytest
from agentrig.core import JsonValue as AgentJson
from agentrig.core import redact_private_trace_content
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.summary_repository import DuckDBSummaryRepository
from opsiu.application.consolidate import (
    summary_agent_input,
    summary_input_hash,
    summary_input_json,
)
from opsiu.application.ports import (
    AnnotationResult,
    DeputyReviewInput,
    JsonValue,
    SummarySpec,
)
from opsiu.domain.errors import DomainError
from opsiu.domain.findings import finding_key

pytestmark = pytest.mark.unit
NOW = datetime(2026, 1, 1, tzinfo=UTC)
MIGRATIONS = Path(__file__).parents[2] / "src/opsiu/adapters/duckdb/migrations"
SPEC = SummarySpec("scripted", "consolidar-deputado", "1", "c" * 64)
OPINION: dict[str, JsonValue] = {
    "fundamento": "Indício sintético sujeito a revisão.",
    "confianca": "baixa",
    "evidencias_citadas": ["registro.cnpj_cpf"],
    "recomendacao": "descartar",
}


def identity(number: int) -> str:
    return f"00000000-0000-7000-8000-{number:012d}"


def plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: plain_json(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain_json(child) for child in value]
    return value


@pytest.fixture
def database() -> Iterator[DuckDBPyConnection]:
    with duckdb.connect() as connection:
        connection.execute("""
            CREATE TABLE runs(run_id UUID PRIMARY KEY, status VARCHAR);
            CREATE TABLE steps(step_id UUID PRIMARY KEY,
                run_id UUID REFERENCES runs(run_id), status VARCHAR);
            CREATE TABLE source_files(source_file_id UUID PRIMARY KEY,
                path VARCHAR, sha256 VARCHAR);
            CREATE TABLE finding_records(
                finding_key VARCHAR PRIMARY KEY, rule_id VARCHAR,
                rule_version VARCHAR, id_documento BIGINT, severity VARCHAR,
                amount DECIMAL(18,2), evidence_json JSON,
                run_id UUID REFERENCES runs(run_id),
                step_id UUID REFERENCES steps(step_id),
                source_file_id UUID REFERENCES source_files(source_file_id),
                record_sha256 VARCHAR, code_commit VARCHAR, created_at TIMESTAMPTZ);
            CREATE TABLE finding_supersessions(
                finding_key VARCHAR PRIMARY KEY REFERENCES finding_records(finding_key),
                superseded_at TIMESTAMPTZ NOT NULL,
                superseded_by_run_id UUID NOT NULL REFERENCES runs(run_id));
            CREATE VIEW findings AS
                SELECT r.finding_key, r.rule_id, r.rule_version, r.id_documento,
                    r.severity, r.amount, r.evidence_json,
                    CASE WHEN s.finding_key IS NULL THEN 'active'
                         ELSE 'superseded' END AS status,
                    r.run_id, r.step_id, r.source_file_id, r.record_sha256,
                    r.code_commit, r.created_at, s.superseded_at,
                    s.superseded_by_run_id
                FROM finding_records r
                LEFT JOIN finding_supersessions s USING (finding_key);
            CREATE TABLE despesas(source_file_id UUID, id_documento BIGINT,
                record_sha256 VARCHAR, numero_deputado_id BIGINT,
                nome_parlamentar VARCHAR, ano INTEGER, mes INTEGER,
                source_record_number BIGINT);
        """)
        # Mirror the immutable foreign-key targets established by 0007a.
        connection.execute(
            (MIGRATIONS / "0007_agents.sql")
            .read_text()
            .replace("REFERENCES findings(", "REFERENCES finding_records(")
        )
        connection.execute((MIGRATIONS / "0008_resumos_deputado.sql").read_text())
        connection.execute(
            "INSERT INTO runs VALUES (?, 'running'), (?, 'running')",
            [identity(1), identity(2)],
        )
        connection.execute(
            "INSERT INTO steps VALUES (?, ?, 'running'), (?, ?, 'running')",
            [identity(3), identity(1), identity(4), identity(2)],
        )
        connection.execute(
            "INSERT INTO source_files VALUES (?, 'original.json', ?), "
            "(?, 'replacement.json', ?)",
            [identity(5), "a" * 64, identity(6), "b" * 64],
        )
        add_finding(connection)
        opinion(connection)
        yield connection


def add_finding(
    database: DuckDBPyConnection,
    *,
    document: int = 101,
    deputy: int = 10,
    name: str = "Nome original",
    month: int = 1,
    status: str = "active",
) -> str:
    key = finding_key("R08", "1", document)
    database.execute(
        "INSERT INTO finding_records VALUES (?, 'R08', '1', ?, 'alta', 10.01, "
        "'{\"origem\":\"sintética\"}', ?, ?, ?, ?, 'commit-original', ?)",
        [
            key,
            document,
            identity(1),
            identity(3),
            identity(5),
            str(document).zfill(64),
            NOW,
        ],
    )
    if status == "superseded":
        database.execute(
            "INSERT INTO finding_supersessions VALUES (?, ?, ?)",
            [key, NOW, identity(1)],
        )
    database.execute(
        "INSERT INTO despesas VALUES (?, ?, ?, ?, ?, 2025, ?, ?)",
        [identity(5), document, str(document).zfill(64), deputy, name, month, document],
    )
    return key


def trace(
    database: DuckDBPyConnection,
    number: int,
    *,
    finding: str | None,
    agent: str,
    prompt: str,
    model: str = "scripted",
    input_json: str = "{}",
    output: dict[str, JsonValue] = OPINION,
    overrides: dict[str, object] | None = None,
) -> str:
    values: dict[str, object] = {
        "agent_trace_id": identity(number),
        "run_id": identity(1),
        "step_id": identity(3),
        "finding_key": finding,
        "agent_id": agent,
        "agent_version": "1",
        "model": model,
        "prompt_name": prompt,
        "prompt_version": "1",
        "prompt_sha256": SPEC.prompt_sha256,
        "input_schema": "input",
        "output_schema": "output",
        "instructions": "Anotar somente as evidências fornecidas.",
        "input_json": input_json,
        "output_json": json.dumps(output),
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
    return identity(number)


def opinion(
    database: DuckDBPyConnection,
    *,
    number: int = 20,
    document: int = 101,
    model: str = "scripted",
    kind: str = "parecer",
    created_at: datetime = NOW,
    trace_overrides: dict[str, object] | None = None,
) -> str:
    key = finding_key("R08", "1", document)
    trace_id = trace(
        database,
        number + 1000,
        finding=key,
        agent="explicar-achado-R08",
        prompt="explicar-achado",
        model=model,
        overrides=trace_overrides,
    )
    database.execute(
        "INSERT INTO pareceres VALUES (?, ?, ?, 'explicar-achado-R08', ?, '1', "
        "?, ?, ?, ?, ?)",
        [
            identity(number),
            key,
            trace_id,
            kind,
            model,
            json.dumps(OPINION),
            created_at,
            identity(1),
            identity(3),
        ],
    )
    return identity(number)


def request(database: DuckDBPyConnection) -> DeputyReviewInput:
    return list(DuckDBSummaryRepository(database).inputs("scripted"))[0]


def summary_trace(
    database: DuckDBPyConnection,
    request: DeputyReviewInput,
    *,
    number: int = 2000,
    spec: SummarySpec = SPEC,
    overrides: dict[str, object] | None = None,
) -> AnnotationResult:
    output: dict[str, JsonValue] = {
        "resumo": "Resumo sintético para revisão humana.",
        "ranking": [
            {
                "finding_key": item.finding.finding_key,
                "severidade": item.finding.severity,
                "justificativa": "Revisar evidência.",
            }
            for item in request.findings
        ],
        "pontos_atencao": ["A recomendação descartar não é uma decisão humana."],
    }
    trace_id = trace(
        database,
        number,
        finding=None,
        agent="consolidar-deputado",
        prompt=spec.prompt_name,
        model=spec.model,
        input_json=json.dumps(
            plain_json(
                redact_private_trace_content(
                    cast(dict[str, AgentJson], summary_agent_input(request))
                )
            )
        ),
        output=output,
        overrides={
            "prompt_version": spec.prompt_version,
            "prompt_sha256": spec.prompt_sha256,
            **(overrides or {}),
        },
    )
    return AnnotationResult(trace_id, output, None)


def save(
    database: DuckDBPyConnection,
    request: DeputyReviewInput,
    result: AnnotationResult,
    *,
    number: int = 3000,
    spec: SummarySpec = SPEC,
    input_hash: str | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
) -> None:
    DuckDBSummaryRepository(database).save(
        request,
        input_hash or summary_input_hash(request),
        spec,
        result,
        summary_id=identity(number),
        created_at=NOW,
        run_id=run_id or identity(1),
        step_id=step_id or identity(3),
    )


def counts(database: DuckDBPyConnection) -> tuple[int, int]:
    row = database.execute(
        "SELECT (SELECT count(*) FROM resumos_deputado), "
        "(SELECT count(*) FROM resumo_fontes)"
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def test_inputs_use_original_identity_latest_opinion_and_all_recommendations(
    database: DuckDBPyConnection,
) -> None:
    add_finding(database, document=102, name="Nome mais recente", month=8)
    opinion(database, number=21, document=102)
    add_finding(database, document=103, deputy=99, name="Outro deputado")
    opinion(database, number=22, document=103)
    add_finding(database, document=104, status="superseded")
    opinion(database, number=23, document=104)
    add_finding(database, document=105)
    newest = opinion(database, number=24, created_at=NOW + timedelta(seconds=1))
    opinion(database, number=25, kind="nota_fiscal", created_at=NOW + timedelta(days=1))
    opinion(database, number=26, model="live-model", created_at=NOW + timedelta(days=1))
    database.execute(
        "INSERT INTO despesas VALUES (?, 101, ?, 999, 'Identidade substituta', "
        "2026, 12, 1)",
        [identity(6), "z" * 64],
    )
    repository = DuckDBSummaryRepository(database)
    groups = list(repository.inputs("scripted"))
    assert [group.numero_deputado_id for group in groups] == [10, 99]
    assert groups[0].nome_parlamentar == "Nome mais recente"
    items = {item.finding.id_documento: item for item in groups[0].findings}
    assert set(items) == {101, 102}
    assert items[101].parecer_id == newest
    assert items[101].parecer["recomendacao"] == "descartar"
    assert items[101].finding.provenance.source.source_file == "original.json"
    assert list(repository.inputs("scripted", deputy_id=99)) == groups[1:]
    assert list(repository.inputs("missing-model")) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("finding_key", None),
        ("agent_id", "wrong-agent"),
        ("model", "wrong-model"),
        ("prompt_name", "revisar-nota"),
        ("prompt_version", "2"),
        ("run_id", identity(2)),
        ("step_id", identity(4)),
        ("output_json", '{"different":true}'),
        ("failure_json", '{"code":"failed"}'),
    ],
)
def test_input_opinion_trace_provenance_and_output_are_verified(
    database: DuckDBPyConnection,
    field: str,
    value: object,
) -> None:
    opinion(
        database,
        number=21,
        created_at=NOW + timedelta(seconds=1),
        trace_overrides={field: value},
    )
    with pytest.raises(DomainError):
        list(DuckDBSummaryRepository(database).inputs("scripted"))


def test_missing_original_record_fails_instead_of_using_replacement(
    database: DuckDBPyConnection,
) -> None:
    database.execute("DELETE FROM despesas")
    with pytest.raises(DomainError, match="original expense"):
        request(database)


def test_saved_summary_retains_exact_sources_and_append_only_history(
    database: DuckDBPyConnection,
) -> None:
    selected = request(database)
    repository = DuckDBSummaryRepository(database)
    digest = summary_input_hash(selected)
    assert not repository.has_success(10, digest, SPEC)
    first = summary_trace(database, selected)
    save(database, selected, first)
    original = database.execute("SELECT * FROM resumos_deputado").fetchall()
    sources = database.execute("SELECT * FROM resumo_fontes").fetchall()
    assert counts(database) == (1, 1)
    assert original[0][9] == summary_input_json(selected)
    assert str(sources[0][2]) == selected.findings[0].parecer_id
    assert str(sources[0][3]) == selected.findings[0].parecer_trace_id
    assert sources[0][5:] == ("a" * 64, str(101).zfill(64))
    assert repository.has_success(10, digest, SPEC)
    assert not repository.has_success(99, digest, SPEC)
    assert not repository.has_success(10, "b" * 64, SPEC)
    assert not repository.has_success(10, digest, replace(SPEC, model="other"))
    assert not repository.has_success(10, digest, replace(SPEC, prompt_version="2"))
    save(
        database, selected, summary_trace(database, selected, number=2001), number=3001
    )
    assert counts(database) == (2, 2)
    assert (
        database.execute(
            "SELECT * FROM resumos_deputado WHERE summary_id = ?", [identity(3000)]
        ).fetchall()
        == original
    )
    with pytest.raises(duckdb.ConstraintException):
        save(database, selected, first, number=3002)
    assert counts(database) == (2, 2)


@pytest.mark.parametrize("inactive", ["run", "step", "other_step", "missing_step"])
def test_save_requires_matching_active_run_and_step(
    database: DuckDBPyConnection,
    inactive: str,
) -> None:
    selected = request(database)
    result = summary_trace(database, selected)
    if inactive == "run":
        database.execute(
            "UPDATE runs SET status = 'completed' WHERE run_id = ?", [identity(1)]
        )
    elif inactive == "step":
        database.execute(
            "UPDATE steps SET status = 'failed' WHERE step_id = ?", [identity(3)]
        )
    with pytest.raises(RuntimeError, match="active run and step"):
        save(
            database,
            selected,
            result,
            step_id={"other_step": identity(4), "missing_step": identity(99)}.get(
                inactive
            ),
        )
    assert counts(database) == (0, 0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("finding_key", finding_key("R08", "1", 101)),
        ("agent_id", "wrong-agent"),
        ("model", "wrong-model"),
        ("prompt_name", "explicar-achado"),
        ("prompt_version", "2"),
        ("prompt_sha256", "d" * 64),
        ("run_id", identity(2)),
        ("step_id", identity(4)),
        ("status", "failed"),
        ("failure_json", '{"code":"failed"}'),
        ("input_json", "{}"),
        ("output_json", '{"changed":true}'),
        ("output_json", None),
    ],
)
def test_summary_trace_must_match_exact_successful_invocation(
    database: DuckDBPyConnection,
    field: str,
    value: object,
) -> None:
    selected = request(database)
    result = summary_trace(database, selected, overrides={field: value})
    with pytest.raises(DomainError):
        save(database, selected, result)
    assert counts(database) == (0, 0)


def test_failed_results_missing_trace_and_wrong_input_hash_never_publish(
    database: DuckDBPyConnection,
) -> None:
    selected = request(database)
    result = summary_trace(database, selected)
    for invalid in (
        replace(result, output=None),
        replace(result, failure={"error": "failed"}),
        replace(result, trace_id=identity(9999)),
    ):
        with pytest.raises(DomainError):
            save(database, selected, invalid)
    with pytest.raises(DomainError, match="input hash"):
        save(database, selected, result, input_hash="d" * 64)
    assert counts(database) == (0, 0)


def test_changed_opinion_identity_changes_hash_and_rejects_stale_input(
    database: DuckDBPyConnection,
) -> None:
    selected = request(database)
    result = summary_trace(database, selected)
    opinion(database, number=21, created_at=NOW + timedelta(seconds=1))
    current = request(database)
    assert current.findings[0].parecer == selected.findings[0].parecer
    assert summary_input_hash(current) != summary_input_hash(selected)
    with pytest.raises(DomainError, match="current eligible input"):
        save(database, selected, result)
    save(database, current, summary_trace(database, current, number=2001))
    assert counts(database) == (1, 1)


@pytest.mark.parametrize("changed", ["superseded", "new_finding"])
def test_active_source_set_cannot_change_between_selection_and_save(
    database: DuckDBPyConnection,
    changed: str,
) -> None:
    selected = request(database)
    result = summary_trace(database, selected)
    if changed == "superseded":
        database.execute(
            "INSERT INTO finding_supersessions SELECT finding_key, ?, ? "
            "FROM findings WHERE status = 'active'",
            [NOW, identity(1)],
        )
    else:
        add_finding(database, document=102)
        opinion(database, number=21, document=102)
    with pytest.raises(DomainError, match="current eligible input"):
        save(database, selected, result)
    assert counts(database) == (0, 0)


@pytest.mark.parametrize(
    "changed",
    [replace(SPEC, prompt_name="changed"), replace(SPEC, prompt_sha256="d" * 64)],
)
def test_same_version_prompt_changes_reject_even_for_new_inputs_or_force(
    database: DuckDBPyConnection,
    changed: SummarySpec,
) -> None:
    selected = request(database)
    save(database, selected, summary_trace(database, selected))
    repository = DuckDBSummaryRepository(database)
    with pytest.raises(DomainError, match="without a version change"):
        repository.has_success(10, "unseen-input", changed)
    changed_result = summary_trace(database, selected, number=2001, spec=changed)
    with pytest.raises(DomainError, match="without a version change"):
        save(database, selected, changed_result, number=3001, spec=changed)
    assert counts(database) == (1, 1)


def test_private_trace_redaction_does_not_change_canonical_source_input(
    database: DuckDBPyConnection,
) -> None:
    database.execute(
        "UPDATE despesas SET nome_parlamentar = 'Bearer secret-credential'"
    )
    selected = request(database)
    result = summary_trace(database, selected)
    save(database, selected, result)
    digest = summary_input_hash(selected)
    assert DuckDBSummaryRepository(database).has_success(10, digest, SPEC)
    stored = database.execute("SELECT input_json FROM resumos_deputado").fetchone()
    private = database.execute(
        "SELECT input_json FROM agent_traces WHERE agent_trace_id = ?",
        [result.trace_id],
    ).fetchone()
    assert stored is not None and private is not None
    assert "secret-credential" in stored[0]
    assert "secret-credential" not in private[0]

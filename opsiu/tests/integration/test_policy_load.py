"""Policy ingestion is run-scoped, exact, idempotent, and preserves dated rows."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.composite_ledger import CompositeLedger
from opsiu.adapters.duckdb.connection import connect_database
from opsiu.adapters.duckdb.ledger import DuckDBLedger
from opsiu.adapters.duckdb.migration_runner import apply_migrations
from opsiu.adapters.duckdb.repository import DuckDBPolicyRepository
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator, collect_metadata
from opsiu.application.ingest import load_policies
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError
from opsiu.domain.policies import ADICIONAIS, CAPS, COTAS_UF

pytestmark = pytest.mark.integration


@pytest.fixture
def database(tmp_path: Path) -> Iterator[DuckDBPyConnection]:
    with connect_database(tmp_path / "policies.duckdb") as connection:
        apply_migrations(connection)
        yield connection


def build_run(connection: DuckDBPyConnection, tmp_path: Path) -> Run:
    return Run(
        ledger=CompositeLedger(
            JsonlLedger(tmp_path / "runs"), DuckDBLedger(connection)
        ),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        command="test policies",
        arguments={},
        metadata=collect_metadata("settings-hash", tmp_path),
    )


def test_policy_load_preserves_exact_values_and_original_provenance_on_repeat(
    database: DuckDBPyConnection, tmp_path: Path
) -> None:
    repository = DuckDBPolicyRepository(database)
    first = build_run(database, tmp_path)
    with first:
        with first.step("policies") as first_step:
            load_policies(first, repository)
    second = build_run(database, tmp_path)
    with second:
        with second.step("policies"):
            load_policies(second, repository)

    assert first.status == second.status == "completed"
    assert database.execute(
        'SELECT category_code, "limit", valid_from FROM caps ORDER BY category_code'
    ).fetchall() == sorted(
        (cap.category_code, cap.limit, cap.valid_from) for cap in CAPS
    )
    assert database.execute(
        "SELECT uf, value, valid_from FROM cota_uf ORDER BY uf"
    ).fetchall() == sorted(
        (quota.uf, quota.value, quota.valid_from) for quota in COTAS_UF
    )
    assert database.execute(
        "SELECT role, value, valid_from FROM adicionais ORDER BY role"
    ).fetchall() == sorted(
        (addition.role, addition.value, addition.valid_from) for addition in ADICIONAIS
    )
    for table in ("caps", "cota_uf", "adicionais"):
        origins = database.execute(
            f"SELECT DISTINCT run_id, step_id FROM {table}"
        ).fetchall()
        assert [(str(run_id), str(step_id)) for run_id, step_id in origins] == [
            (first.run_id, first_step.record.step_id)
        ]
    assert second.counters == {
        "policy_caps": 6,
        "policy_quotas": 27,
        "policy_additions": 14,
    }
    events = DuckDBLedger(database).get_events(second.run_id)
    assert any(event.kind == "policies.loaded" for event in events)


def test_new_effective_dates_preserve_older_versions(
    database: DuckDBPyConnection, tmp_path: Path
) -> None:
    repository = DuckDBPolicyRepository(database)
    run = build_run(database, tmp_path)
    with run:
        with run.step("policies"):
            load_policies(run, repository)
            updated = replace(
                CAPS[0], limit=Decimal("13000.00"), valid_from=date(2024, 1, 1)
            )
            repository.upsert_policies(
                (updated,), (), (), run_id=run.run_id, step_id=run.require_step()
            )

    assert database.execute(
        'SELECT "limit", valid_from FROM caps '
        "WHERE category_code = ? ORDER BY valid_from",
        [CAPS[0].category_code],
    ).fetchall() == [
        (Decimal("12713.00"), date(2023, 1, 1)),
        (Decimal("13000.00"), date(2024, 1, 1)),
    ]


def test_conflicting_version_rolls_back_the_whole_batch(
    database: DuckDBPyConnection, tmp_path: Path
) -> None:
    repository = DuckDBPolicyRepository(database)
    run = build_run(database, tmp_path)
    with run:
        with run.step("initial"):
            load_policies(run, repository)
        with run.step("conflict"):
            new = replace(CAPS[0], valid_from=date(2024, 1, 1))
            conflict = replace(CAPS[0], limit=Decimal("1.00"))
            with pytest.raises(DomainError, match="conflicting policy version"):
                repository.upsert_policies(
                    (new, conflict),
                    (),
                    (),
                    run_id=run.run_id,
                    step_id=run.require_step(),
                )

    assert database.execute("SELECT count(*) FROM caps").fetchone() == (6,)
    assert database.execute(
        'SELECT "limit" FROM caps WHERE category_code = ?', [CAPS[0].category_code]
    ).fetchone() == (Decimal("12713.00"),)


def test_load_requires_active_run_and_step_before_any_write(
    database: DuckDBPyConnection, tmp_path: Path
) -> None:
    repository = DuckDBPolicyRepository(database)
    run = build_run(database, tmp_path)
    with pytest.raises(RuntimeError, match="not active"):
        load_policies(run, repository)
    with run:
        with pytest.raises(RuntimeError, match="active run step"):
            load_policies(run, repository)
        with run.step("closed") as step:
            pass
        with pytest.raises(RuntimeError, match="matching active run and step"):
            repository.upsert_policies(
                CAPS,
                COTAS_UF,
                ADICIONAIS,
                run_id=run.run_id,
                step_id=step.record.step_id,
            )
    with pytest.raises(RuntimeError, match="matching active run and step"):
        repository.upsert_policies(
            CAPS, COTAS_UF, ADICIONAIS, run_id=run.run_id, step_id=step.record.step_id
        )
    assert database.execute("SELECT count(*) FROM caps").fetchone() == (0,)

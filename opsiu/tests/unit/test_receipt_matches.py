"""Verified cache growth must not rewrite an existing immutable receipt proof."""

import json
from copy import deepcopy
from pathlib import Path

import duckdb
import pytest
from duckdb import DuckDBPyConnection

from opsiu.adapters.duckdb.receipt_matches import (
    _preserve_active_evidence,
    _require_unambiguous_receipt_origins,
    prepare_receipt_matches,
)
from opsiu.adapters.files.jsonl_log import JsonlLedger
from opsiu.adapters.system import SystemClock, Uuid7Generator
from opsiu.application.ports import JsonValue, RunMetadata
from opsiu.application.runs import Run
from opsiu.domain.errors import DomainError

pytestmark = pytest.mark.unit


def group(*documents: int) -> dict[str, JsonValue]:
    receipts: list[JsonValue] = [
        {
            "id_documento": document,
            "url": f"https://example.test/{document}",
            "path": f"/cache/pdf/{document}.pdf",
            "size_bytes": 100,
            "sha256": "a" * 64,
            "snapshot_id": f"snapshot-{document}",
            "snapshot_sha256": "b" * 64,
        }
        for document in documents
    ]
    return {
        "documentos": list(documents),
        "tamanho_bytes": 100,
        "sha256": "a" * 64,
        "comparacao_bytes": True,
        "recibos": receipts,
    }


def database() -> DuckDBPyConnection:
    db = duckdb.connect()
    db.execute(
        "CREATE TABLE findings (id_documento BIGINT, rule_id VARCHAR, "
        "rule_version VARCHAR, status VARCHAR, evidence_json JSON)"
    )
    return db


def save(
    db: DuckDBPyConnection,
    document: int,
    groups: list[dict[str, JsonValue]],
    *,
    version: str = "3",
    status: str = "active",
) -> None:
    db.execute(
        "INSERT INTO findings VALUES (?, 'R09', ?, ?, ?)",
        [
            document,
            version,
            status,
            json.dumps({"comprovantes_identicos": groups}),
        ],
    )


def test_larger_verified_group_keeps_original_pair_proof_for_existing_findings() -> (
    None
):
    original = group(1, 2)
    current = group(1, 2, 3)
    with database() as db:
        save(db, 1, [original])
        save(db, 2, [original])
        matches = {document: [deepcopy(current)] for document in (1, 2, 3)}
        assert _preserve_active_evidence(db, matches) == 2
        assert matches[1] == matches[2] == [original]
        assert matches[3] == [current]
        # Repeating the run preserves the same evidence again.
        assert _preserve_active_evidence(db, matches) == 2


@pytest.mark.parametrize(
    "changed_field", ["url", "path", "snapshot_id", "snapshot_sha256"]
)
def test_same_bytes_with_changed_origin_cannot_reuse_old_proof(
    changed_field: str,
) -> None:
    original = group(1, 2)
    current = group(1, 2, 3)
    receipts = current["recibos"]
    assert isinstance(receipts, list) and isinstance(receipts[0], dict)
    receipts[0][changed_field] = "changed-origin"
    with database() as db:
        save(db, 1, [original])
        matches = {1: [deepcopy(current)]}
        assert _preserve_active_evidence(db, matches) == 0
        assert matches[1] == [current]


def test_old_partners_must_remain_together_in_the_verified_group() -> None:
    original = group(1, 2)
    current = group(1, 3)
    with database() as db:
        save(db, 1, [original])
        matches = {1: [deepcopy(current)]}
        assert _preserve_active_evidence(db, matches) == 0
        assert matches == {1: [current]}


def test_every_original_group_must_remain_verified() -> None:
    first, second = group(1, 2), group(1, 3)
    with database() as db:
        save(db, 1, [first, second])
        matches = {1: [deepcopy(first)]}
        assert _preserve_active_evidence(db, matches) == 0
        assert matches == {1: [first]}


def test_old_versions_and_superseded_findings_do_not_pin_new_evidence() -> None:
    original, current = group(1, 2), group(1, 2, 3)
    with database() as db:
        save(db, 1, [original], version="2")
        save(db, 2, [original], status="superseded")
        matches = {1: [deepcopy(current)], 2: [deepcopy(current)]}
        assert _preserve_active_evidence(db, matches) == 0
        assert matches == {1: [current], 2: [current]}


def test_original_group_cannot_omit_provenance_for_claimed_partner() -> None:
    original, current = group(1, 2), group(1, 2, 3)
    receipts = original["recibos"]
    assert isinstance(receipts, list)
    original["recibos"] = receipts[:1]
    with database() as db:
        save(db, 1, [original])
        matches = {1: [deepcopy(current)]}
        assert _preserve_active_evidence(db, matches) == 0
        assert matches == {1: [current]}


def test_accounting_wrapper_keeps_exact_original_proof_after_cache_growth() -> None:
    original, current = group(1, 2), group(1, 2, 3)
    wrapper = {
        "ocorrencias": [
            {
                "valor": "100.00",
                "evidencia": {"comprovantes_identicos": [original]},
            }
        ],
        "registros_origem": [
            {"registro_sha256": "c" * 64},
            {"registro_sha256": "d" * 64},
        ],
    }
    with database() as db:
        serialized = json.dumps(wrapper, sort_keys=True)
        db.execute(
            "INSERT INTO findings VALUES (1, 'R09', '3', 'active', ?)", [serialized]
        )
        matches = {1: [deepcopy(current)], 3: [deepcopy(current)]}
        assert _preserve_active_evidence(db, matches) == 1
        # Return only the proof; check rebuilds its accounting wrapper itself.
        assert matches[1] == [original]
        assert matches[3] == [current]
        assert db.execute("SELECT evidence_json FROM findings").fetchone() == (
            serialized,
        )


@pytest.mark.parametrize("problem", ["two-occurrences", "top-and-nested", "invalid"])
def test_ambiguous_or_invalid_wrapped_proof_blocks_preservation(problem: str) -> None:
    original = group(1, 2)
    proof: dict[str, JsonValue] = {"comprovantes_identicos": [original]}
    wrapped: dict[str, JsonValue] = {
        "ocorrencias": [{"valor": "100.00", "evidencia": proof}]
    }
    if problem == "two-occurrences":
        wrapped["ocorrencias"] = [
            {"valor": "100.00", "evidencia": proof},
            {"valor": "100.00", "evidencia": proof},
        ]
    elif problem == "top-and-nested":
        wrapped["comprovantes_identicos"] = [original]
    else:
        proof["comprovantes_identicos"] = "invalid"
    with database() as db:
        db.execute(
            "INSERT INTO findings VALUES (1, 'R09', '3', 'active', ?)",
            [json.dumps(wrapped)],
        )
        matches = {1: [group(1, 2, 3)]}
        with pytest.raises(DomainError):
            _preserve_active_evidence(db, matches)


@pytest.mark.parametrize(
    "urls", [["https://a", "https://b"], ["https://a", None], ["https://a", ""], [None]]
)
def test_cached_document_with_ambiguous_current_urls_blocks_check_with_event(
    tmp_path: Path, urls: list[str | None]
) -> None:
    with database() as db:
        db.execute(
            "CREATE TABLE despesas_current(id_documento BIGINT, url_documento VARCHAR)"
        )
        db.execute("CREATE TABLE nf_texto(id_documento BIGINT)")
        db.execute("INSERT INTO nf_texto VALUES (1)")
        db.executemany(
            "INSERT INTO despesas_current VALUES (1, ?)", [(url,) for url in urls]
        )
        ledger = JsonlLedger(tmp_path / "runs")
        run = Run(
            ledger=ledger,
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="test-receipt-origins",
            arguments={},
            metadata=RunMetadata("settings", "commit", False, "3.12", "0.1.0"),
        )
        with run:
            with run.step("comparar_comprovantes"):
                with pytest.raises(DomainError, match="URLs correntes"):
                    prepare_receipt_matches(db, tmp_path, run)
        events = [
            json.loads(line)
            for line in (ledger.run_dir(run.run_id) / "events.jsonl")
            .read_text()
            .splitlines()
        ]
        ambiguous = [
            event for event in events if event["kind"] == "receipts.ambiguous_document"
        ]
        assert len(ambiguous) == 1
        assert ambiguous[0]["attributes"]["id_documento"] == 1


def test_repeated_same_url_is_unambiguous_and_uncached_documents_are_out_of_scope(
    tmp_path: Path,
) -> None:
    with database() as db:
        db.execute(
            "CREATE TABLE despesas_current(id_documento BIGINT, url_documento VARCHAR)"
        )
        db.execute("CREATE TABLE nf_texto(id_documento BIGINT)")
        db.execute("INSERT INTO nf_texto VALUES (1)")
        db.execute(
            "INSERT INTO despesas_current VALUES "
            "(1,'https://a'),(1,'https://a'),(2,'https://b'),(2,'https://c'),(2,NULL)"
        )
        run = Run(
            ledger=JsonlLedger(tmp_path / "runs"),
            clock=SystemClock(),
            ids=Uuid7Generator(),
            command="test-receipt-origins",
            arguments={},
            metadata=RunMetadata("settings", "commit", False, "3.12", "0.1.0"),
        )
        with run:
            with run.step("comparar_comprovantes"):
                _require_unambiguous_receipt_origins(db, run)
        assert run.status == "completed"

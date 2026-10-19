"""Reproducible synthetic expenses and strict advisory recommendation labels."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

type Recommendation = Literal["manter", "descartar", "revisar"]

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "tests/fixtures/mini_dataset/Ano-2099.json"
)
CASES_PATH = Path(__file__).resolve().with_name("labeled_findings.jsonl")
FIXTURE_SHA256 = "927f5b11ce4d7488e71f39b885c5e6377ad1ad2fd2c27ae8ad5b19176de5e5d4"
LOCAL_RULE_IDS = (*(f"R{number:02d}" for number in range(1, 11)), "R14")
REPLICA_COUNT = 4
DOCUMENT_ID_STRIDE = 100_000
DEPUTY_ID_STRIDE = 10_000


@dataclass(frozen=True, slots=True)
class EvalCase:
    finding_key: str
    expected_recommendation: Recommendation
    notes: str

    def __post_init__(self) -> None:
        if not isinstance(self.finding_key, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.finding_key
        ):
            raise ValueError("finding_key must be a lowercase SHA-256 identity")
        if self.expected_recommendation not in ("manter", "descartar", "revisar"):
            raise ValueError("expected_recommendation is not a supported label")
        if not isinstance(self.notes, str) or not self.notes.strip():
            raise ValueError("notes must explain the synthetic label assumption")


def build_expense_rows() -> tuple[dict[str, object], ...]:
    """Replicate the pinned 200-row fixture without cross-replica grouping.

    The file is a small checked-in synthetic fixture, never a production yearly
    file. Each call returns independent dictionaries in replica/source order.
    Only document/deputy IDs, invoice numbers and synthetic URLs are changed.
    """
    content = FIXTURE_PATH.read_bytes()
    if hashlib.sha256(content).hexdigest() != FIXTURE_SHA256:
        raise ValueError(
            "mini fixture changed; review the evaluation recipe and labels"
        )
    source = cast(dict[str, list[dict[str, object]]], json.loads(content))["dados"]
    result: list[dict[str, object]] = []
    for replica in range(REPLICA_COUNT):
        for original in source:
            row = dict(original)
            document_id = int(str(row["idDocumento"])) + replica * DOCUMENT_ID_STRIDE
            row["idDocumento"] = str(document_id)
            row["numeroDeputadoID"] = (
                int(str(row["numeroDeputadoID"])) + replica * DEPUTY_ID_STRIDE
            )
            row["numero"] = f"EVAL-{replica + 1}-{row['numero']}"
            row["urlDocumento"] = f"https://example.invalid/opsiu/{document_id}.pdf"
            result.append(row)
    return tuple(result)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_cases(path: Path | None = None) -> tuple[EvalCase, ...]:
    """Read ordered, nonempty JSONL cases without coercion or duplicate keys.

    The default path is anchored to this module, independent of the current
    directory. Labels are grader references, never agent input or scenarios.
    """
    source = CASES_PATH if path is None else path
    cases: list[EvalCase] = []
    keys: set[str] = set()
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                raw: object = json.loads(line, object_pairs_hook=_unique_object)
                if not isinstance(raw, dict) or set(raw) != {
                    "finding_key",
                    "expected_recommendation",
                    "notes",
                }:
                    raise ValueError(
                        "case must contain exactly the three schema fields"
                    )
                if not all(isinstance(value, str) for value in raw.values()):
                    raise ValueError("case fields must be strings")
                case = EvalCase(
                    raw["finding_key"],
                    cast(Recommendation, raw["expected_recommendation"]),
                    raw["notes"],
                )
                if case.finding_key in keys:
                    raise ValueError("duplicate finding_key")
            except ValueError as error:
                raise ValueError(
                    f"invalid eval case at line {line_number}: {error}"
                ) from error
            cases.append(case)
            keys.add(case.finding_key)
    if not cases:
        raise ValueError("evaluation dataset must contain at least one case")
    return tuple(cases)

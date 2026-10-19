"""Stable finding identity and immutable evidence provenance."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


def finding_key(rule_id: str, rule_version: str, id_documento: int) -> str:
    identity = json.dumps([rule_id, rule_version, id_documento], separators=(",", ":"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _evidence_scalar(value: object) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("evidence contains a non-finite Decimal")
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported evidence scalar: {type(value).__name__}")


def evidence_json(evidence: Mapping[str, object]) -> str:
    """Canonical JSON with exact decimal/date strings, never binary money floats."""
    return json.dumps(
        dict(evidence),
        default=_evidence_scalar,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class SourceOrigin:
    source_file_id: str
    source_file: str
    source_sha256: str
    record_sha256: str


@dataclass(frozen=True, slots=True)
class Provenance:
    run_id: str
    step_id: str
    source: SourceOrigin
    code_commit: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    rule_version: str
    id_documento: int
    severity: str
    amount: Decimal
    evidence: Mapping[str, object]
    provenance: Provenance
    status: Literal["active", "superseded"] = "active"
    superseded_at: datetime | None = None
    superseded_by_run_id: str | None = None

    @property
    def finding_key(self) -> str:
        return finding_key(self.rule_id, self.rule_version, self.id_documento)

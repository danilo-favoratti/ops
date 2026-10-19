"""Canonical raw-record hashing shared by ingestion and the domain model."""

import hashlib
import json
from collections.abc import Mapping

from opsiu.domain.errors import DomainError


def record_sha256(raw: Mapping[str, object]) -> str:
    """Hash unnormalized JSON, with sorted keys, UTF-8, and no extra whitespace."""
    try:
        canonical = json.dumps(
            dict(raw),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise DomainError("raw expense must contain valid JSON values") from error
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

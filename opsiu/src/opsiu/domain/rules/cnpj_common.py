"""Shared source-coverage validation for filtered Receita rules."""

from collections.abc import Mapping

from opsiu.domain.errors import DomainError


def require_cnpj_coverage(row: Mapping[str, object]) -> None:
    if row["snapshot_id"] is None or row["covered_root"] is None:
        raise DomainError(
            f"Receita data has not been successfully loaded for root {row['cnpj_root']}"
        )

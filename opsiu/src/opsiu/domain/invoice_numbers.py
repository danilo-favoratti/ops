"""Invoice-number eligibility without changing meaningful invoice identifiers."""

from opsiu.domain.text import normalize_name, normalized_name_sql

INVOICE_NUMBER_PLACEHOLDERS = ("SN", "SNO", "ND", "SEMNUMERO")


def usable_invoice_number(value: object) -> bool:
    """Reject missing-number markers; keep the original value for exact matching."""
    if not isinstance(value, str):
        return False
    normalized = normalize_name(value).replace(" ", "")
    return (
        bool(normalized)
        and normalized not in INVOICE_NUMBER_PLACEHOLDERS
        and (set(normalized) != {"0"})
    )


def usable_invoice_number_sql(column: str) -> str:
    """Render the same eligibility policy for a trusted SQL column identifier."""
    normalized = f"replace({normalized_name_sql(column)}, ' ', '')"
    placeholders = ", ".join(f"'{value}'" for value in INVOICE_NUMBER_PLACEHOLDERS)
    return (
        f"({column} IS NOT NULL AND {normalized} <> '' "
        f"AND {normalized} NOT IN ({placeholders}) "
        f"AND NOT regexp_full_match({normalized}, '0+'))"
    )

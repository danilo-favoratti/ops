"""Deterministic normalization shared by public names and matching rules."""

import re
import unicodedata


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value)
    unaccented = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"[^A-Za-z0-9]+", " ", unaccented).strip().upper()


def normalized_name_sql(column: str) -> str:
    """Render the same normalization for a trusted SQL column identifier."""
    if re.fullmatch(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?", column) is None:
        raise ValueError("normalization requires a SQL column identifier")
    return (
        f"upper(trim(regexp_replace(strip_accents({column}), "
        "'[^A-Za-z0-9]+', ' ', 'g')))"
    )

"""Pure checks for the numeric supplier identities supported by the importer."""

_CNPJ_WEIGHTS = (
    (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
    (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
)


def is_valid_numeric_cnpj(value: object) -> bool:
    """Check numeric CNPJ syntax and both digits, without repairing placeholders."""
    if (
        not isinstance(value, str)
        or len(value) != 14
        or not value.isascii()
        or not value.isdigit()
        or len(set(value)) == 1
    ):
        return False
    digits = tuple(int(char) for char in value)
    for index, weights in enumerate(_CNPJ_WEIGHTS, start=12):
        remainder = (
            sum(
                digit * weight
                for digit, weight in zip(digits[:index], weights, strict=True)
            )
            % 11
        )
        if digits[index] != (0 if remainder < 2 else 11 - remainder):
            return False
    return True


def valid_numeric_cnpj_sql(column: str) -> str:
    """Build the equivalent DuckDB predicate for a trusted internal SQL column."""
    checks = [
        f"regexp_full_match({column}, '[0-9]{{14}}')",
        f"{column} <> repeat(substr({column}, 1, 1), 14)",
    ]
    for index, weights in enumerate(_CNPJ_WEIGHTS, start=13):
        weighted_sum = " + ".join(
            f"try_cast(substr({column}, {position}, 1) AS INTEGER) * {weight}"
            for position, weight in enumerate(weights, start=1)
        )
        remainder = f"(({weighted_sum}) % 11)"
        checks.append(
            f"try_cast(substr({column}, {index}, 1) AS INTEGER) = "
            f"CASE WHEN {remainder} < 2 THEN 0 ELSE 11 - {remainder} END"
        )
    return " AND ".join(checks)

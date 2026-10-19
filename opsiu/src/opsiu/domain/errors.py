"""Validation failures in source values and domain objects."""


class DomainError(ValueError):
    """A source value cannot be represented safely in the audit domain."""

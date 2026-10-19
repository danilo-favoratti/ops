"""The single ordered registry of implemented deterministic rules."""

from opsiu.domain.rules.base import Rule

RULES: tuple[type[Rule], ...] = ()


def get_rule(id: str) -> Rule:
    matches = [rule for rule in RULES if rule.descriptor.id == id]
    if len(matches) != 1:
        raise KeyError(f"unknown or duplicated rule: {id}")
    return matches[0]()

"""Immutable human review decisions, independent of storage and agents."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DecisionValue(StrEnum):
    APPROVED = "aprovado"
    REJECTED = "rejeitado"
    REVIEW = "revisar"


def parse_decision(value: object) -> DecisionValue:
    if isinstance(value, str):
        try:
            return DecisionValue(value)
        except ValueError:
            pass
    raise ValueError("A decisão deve ser aprovado, rejeitado ou revisar.")


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    finding_key: str
    decision: DecisionValue
    reviewer: str
    comment: str
    decided_at: datetime
    run_id: str
    step_id: str

    def __post_init__(self) -> None:
        for name in ("decision_id", "finding_key", "reviewer", "run_id", "step_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"O campo {name} deve conter texto não vazio.")
        object.__setattr__(self, "decision", parse_decision(self.decision))
        if not isinstance(self.comment, str):
            raise ValueError("O comentário deve ser texto.")
        if (
            not isinstance(self.decided_at, datetime)
            or self.decided_at.tzinfo is None
            or self.decided_at.utcoffset() is None
        ):
            raise ValueError("O horário da decisão deve incluir o fuso horário.")

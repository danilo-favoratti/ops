"""Contextual display labels without changing stored agent annotations."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class ExplanationDisplay:
    rule_id: str
    rule_title: str
    deputy_id: int
    deputy_name: str
    category_id: int
    category_name: str | None

    def __call__(self, text: str) -> str:
        replacements = [
            (rf"\bregra\s+{re.escape(self.rule_id)}\b", f"regra ‘{self.rule_title}’"),
            (
                rf"\bparlamentar\s+[\"']?{self.deputy_id}[\"']?(?!\w)",
                f"parlamentar {self.deputy_name}",
            ),
        ]
        if self.category_name:
            replacements.append(
                (
                    rf"\b(?:subcota|categoria)\s+[\"']?{self.category_id}[\"']?(?!\w)",
                    f"categoria ‘{self.category_name}’",
                )
            )
        for pattern, label in replacements:
            text = re.sub(pattern, lambda _: label, text, flags=re.IGNORECASE)
        text = re.sub(
            r'["\'](-?\d+\.\d{2})["\'](?= menos a glosa\b)',
            lambda match: money(match[1]),
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b(total|limite|valor(?: envolvido| líquido| do documento)?"
            r"(?: fornecido)?|"
            r"glosa(?: informada)?|resulta em)((?: (?:é|de))?\s+)"
            r'["\']?(-?\d+\.\d{2})(?!\d)["\']?',
            lambda match: f"{match[1]}{match[2]}{money(match[3])}",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r'(?<!\w)["\']?(\d{4}-\d{2}-\d{2})["\']?(?!\w)', _date, text)
        text = re.sub(
            r"\bcompetência (\d{4})/(0?[1-9]|1[0-2])(?!\d)",
            lambda match: f"competência {int(match[2]):02d}/{match[1]}",
            text,
            flags=re.IGNORECASE,
        )
        return text.replace(
            "`texto_nota` é nulo", "O texto da nota não está disponível"
        )


def money(value: str | Decimal) -> str:
    formatted = f"{Decimal(value):,.2f}"
    return "R$ " + formatted.translate(str.maketrans(",.", ".,"))


def _date(match: re.Match[str]) -> str:
    try:
        return date.fromisoformat(match[1]).strftime("%d/%m/%Y")
    except ValueError:
        return match[0]

"""Explicit fictional policies for pipeline tests using the year-2099 dataset."""

from datetime import date
from decimal import Decimal

import pytest

from opsiu.domain.researched_caps import MonthlyCapPolicy
from opsiu.domain.rules.r01_locacao_veiculos import R01LocacaoVeiculos
from opsiu.domain.rules.r05_cursos_eventos import R05CursosEventos


@pytest.fixture
def synthetic_cap_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in only: exercise workflow mechanics without asserting future law."""
    for rule, limit in (
        (R01LocacaoVeiculos, Decimal("12713.00")),
        (R05CursosEventos, Decimal("7697.17")),
    ):
        monkeypatch.setattr(
            rule,
            "policies",
            (
                MonthlyCapPolicy(
                    start=date(2099, 1, 1),
                    end=date(2103, 1, 1),
                    limit=limit,
                    legal_effective=date(2099, 1, 1),
                    formula="Limite fictício para testar o fluxo; não é legislação.",
                    sources=("https://example.invalid/synthetic-cap-policy",),
                ),
            ),
        )

"""Researched cap periods, kept separate from immutable legacy policy rows."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

LEGISLATION = "https://www2.camara.leg.br/legin/int/atomes/"
RENTAL_2017_SOURCE = (
    LEGISLATION
    + "2017/atodamesa-183-16-maio-2017-784814-publicacaooriginal-152684-cd-mesa.html"
)
RENTAL_2025_SOURCE = (
    LEGISLATION
    + "2025/atodamesa-196-27-maio-2025-797512-publicacaooriginal-175496-cd-mesa.html"
)
RENTAL_2026_SOURCE = (
    LEGISLATION
    + "2026/atodamesa-246-2-marco-2026-798777-publicacaooriginal-178340-cd-mesa.html"
)
QUOTA_2016_SOURCE = (
    LEGISLATION
    + "2016/atodamesa-76-3-fevereiro-2016-782328-publicacaooriginal-149360-cd-mesa.html"
)
QUOTA_2023_SOURCE = (
    LEGISLATION
    + "2023/atodamesa-270-19-janeiro-2023-793708-publicacaooriginal-166895-cd-mesa.html"
)
QUOTA_2023_ANNEX = (
    LEGISLATION + "2023/atodamesa-270-19-janeiro-2023-793708-anexo-cd-mesa.pdf"
)
QUOTA_2026_SOURCE = (
    LEGISLATION + "2026/atodamesa-244-20-fevereiro-2026-798720-"
    "publicacaooriginal-178157-cd-mesa.html"
)
QUOTA_CURRENT_SOURCE = (
    "https://www2.camara.leg.br/comunicacao/assessoria-de-imprensa/"
    "guia-para-jornalistas/cota-parlamentar"
)
COURSE_SOURCE = (
    LEGISLATION + "2009/atodamesa-43-21-maio-2009-588364-normaatualizada-cd-mesa.html"
)
RENTAL_IPCA_SOURCE = (
    "https://apisidra.ibge.gov.br/values/t/1737/n1/all/v/2266/p/201704,202502"
)


def rounded_cents(value: Decimal) -> Decimal:
    """Use an explicit reproducible cent convention, including half-cent caps."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class MonthlyCapPolicy:
    """A researched competence range, not a rewrite of an earlier policy row."""

    start: date
    end: date
    limit: Decimal
    legal_effective: date
    formula: str
    sources: tuple[str, ...]
    limitation: str | None = None
    transition_limit: Decimal | None = None
    change_effective: date | None = None


RENTAL_UPDATED_LIMIT = rounded_cents(
    Decimal("12713.00") * Decimal("7205.03") / Decimal("4828.44")
)
RENTAL_UPDATED_FORMULA = "12713.00 × IPCA(2025-02: 7205.03) / IPCA(2017-04: 4828.44)"
RENTAL_UPDATED_SOURCES = (RENTAL_2025_SOURCE, RENTAL_IPCA_SOURCE)

# The audit coverage is explicit. Extending it requires checking later amendments.
# start/end are monthly competence boundaries; legal_effective is the actual date.
RENTAL_CAP_POLICIES: tuple[MonthlyCapPolicy, ...] = (
    MonthlyCapPolicy(
        date(2023, 1, 1),
        date(2025, 5, 1),
        Decimal("12713.00"),
        date(2017, 5, 17),
        "12713.00",
        (RENTAL_2017_SOURCE,),
    ),
    MonthlyCapPolicy(
        date(2025, 5, 1),
        date(2025, 6, 1),
        Decimal("12713.00"),
        date(2017, 5, 17),
        "Referência anterior: 12713.00; novo limite: " + RENTAL_UPDATED_FORMULA,
        (RENTAL_2017_SOURCE, *RENTAL_UPDATED_SOURCES),
        "A atualização entrou em vigor em 28/05/2025. A competência mensal "
        "não permite determinar qual limite se aplica a cada despesa. "
        "O valor sinalizado é a diferença para a referência anterior, "
        "não um excesso confirmado.",
        RENTAL_UPDATED_LIMIT,
        date(2025, 5, 28),
    ),
    MonthlyCapPolicy(
        date(2025, 6, 1),
        date(2026, 3, 1),
        RENTAL_UPDATED_LIMIT,
        date(2025, 5, 28),
        RENTAL_UPDATED_FORMULA,
        RENTAL_UPDATED_SOURCES,
    ),
    MonthlyCapPolicy(
        date(2026, 3, 1),
        date(2027, 1, 1),
        RENTAL_UPDATED_LIMIT,
        date(2025, 5, 28),
        RENTAL_UPDATED_FORMULA,
        (*RENTAL_UPDATED_SOURCES, RENTAL_2026_SOURCE),
        "Desde 03/03/2026 existe previsão de reembolso adicional vinculado "
        "ao art. 2º, §§ 3º e 5º. A fonte não informa a autorização ou o saldo "
        "individual aplicável. A diferença para o limite básico exige revisão "
        "e não comprova excesso; março também é mês de transição.",
        change_effective=date(2026, 3, 3),
    ),
)

COURSE_CAP_POLICIES: tuple[MonthlyCapPolicy, ...] = (
    MonthlyCapPolicy(
        date(2023, 1, 1),
        date(2023, 2, 1),
        rounded_cents(Decimal("30788.66") * Decimal("0.25")),
        date(2016, 3, 1),
        "30788.66 × 0.25 = 7697.165",
        (COURSE_SOURCE, QUOTA_2016_SOURCE),
    ),
    MonthlyCapPolicy(
        date(2023, 2, 1),
        date(2026, 2, 1),
        rounded_cents(Decimal("36582.46") * Decimal("0.25")),
        date(2023, 2, 1),
        "36582.46 × 0.25 = 9145.615",
        (COURSE_SOURCE, QUOTA_2023_SOURCE, QUOTA_2023_ANNEX),
    ),
    MonthlyCapPolicy(
        date(2026, 2, 1),
        date(2026, 3, 1),
        rounded_cents(Decimal("36582.46") * Decimal("0.25")),
        date(2023, 2, 1),
        "Referência anterior: 36582.46 × 0.25 = 9145.615; "
        "novo limite: 41612.55 × 0.25 = 10403.1375",
        (
            COURSE_SOURCE,
            QUOTA_2023_SOURCE,
            QUOTA_2023_ANNEX,
            QUOTA_2026_SOURCE,
            QUOTA_CURRENT_SOURCE,
        ),
        "A atualização da cota entrou em vigor em 20/02/2026. A competência "
        "mensal não resolve a aplicação do novo limite. A diferença para "
        "a referência anterior exige revisão e não comprova excesso.",
        rounded_cents(Decimal("41612.55") * Decimal("0.25")),
        date(2026, 2, 20),
    ),
    MonthlyCapPolicy(
        date(2026, 3, 1),
        date(2027, 1, 1),
        rounded_cents(Decimal("41612.55") * Decimal("0.25")),
        date(2026, 2, 20),
        "41612.55 × 0.25 = 10403.1375",
        (COURSE_SOURCE, QUOTA_2026_SOURCE, QUOTA_CURRENT_SOURCE),
    ),
)

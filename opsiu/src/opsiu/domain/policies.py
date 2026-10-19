"""Published CEAP caps, state quotas, and non-cumulative monthly role additions."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Cap:
    category_code: int
    limit: Decimal
    valid_from: date


@dataclass(frozen=True, slots=True)
class CotaUF:
    uf: str
    value: Decimal
    valid_from: date


@dataclass(frozen=True, slots=True)
class Adicional:
    role: str
    value: Decimal
    valid_from: date


# Historical assumption: apply the current published values to 2023 onward.
# This is not a researched history of legal effective dates; see ADR 0008.
POLICY_VALID_FROM = date(2023, 1, 1)

# First-round dates define the explicitly planned 120-day publicity window.
ELECTION_DATES = MappingProxyType({2024: date(2024, 10, 6), 2026: date(2026, 10, 4)})

# Conservative review heuristics, not claims of kinship or invoice contents.
R12_COMMON_SURNAMES = frozenset(
    {
        "ALMEIDA",
        "ARAUJO",
        "BARBOSA",
        "CARVALHO",
        "COSTA",
        "DIAS",
        "FERREIRA",
        "FILHO",
        "GOMES",
        "JUNIOR",
        "LIMA",
        "MARTINS",
        "NASCIMENTO",
        "NETO",
        "OLIVEIRA",
        "PEREIRA",
        "RIBEIRO",
        "ROCHA",
        "RODRIGUES",
        "SANTOS",
        "SILVA",
        "SOBRINHO",
        "SOUSA",
        "SOUZA",
    }
)
GROCERY_CNAES = frozenset({"4711301", "4711302", "4712100"})
GROCERY_NAME_TOKENS = frozenset(
    {
        "SUPERMERCADO",
        "SUPERMERCADOS",
        "HIPERMERCADO",
        "HIPERMERCADOS",
        "MINIMERCADO",
        "MINIMERCADOS",
        "MERCEARIA",
        "MERCEARIAS",
    }
)
DRIVER_CNAES = frozenset({"4923002"})
DRIVER_NAME_PHRASES = ("COM MOTORISTA", "SERVICO DE MOTORISTA", "SERVICOS DE MOTORISTA")

# numeroSubCota/descricao pairs verified in the 2023 and 2026 source files:
# 120: LOCAÇÃO OU FRETAMENTO DE VEÍCULOS AUTOMOTORES
# 122: SERVIÇO DE TÁXI, PEDÁGIO E ESTACIONAMENTO
#   3: COMBUSTÍVEIS E LUBRIFICANTES.
#   8: SERVIÇO DE SEGURANÇA PRESTADO POR EMPRESA ESPECIALIZADA.
# 137: PARTICIPAÇÃO EM CURSO, PALESTRA OU EVENTO SIMILAR
#  40: COMPLEMENTAÇÃO DO AUXÍLIO-MORADIA
CAP_CATEGORY_CODES = MappingProxyType(
    {"R01": 120, "R02": 122, "R03": 3, "R04": 8, "R05": 137, "R06": 40}
)

CAPS = (
    Cap(CAP_CATEGORY_CODES["R01"], Decimal("12713.00"), POLICY_VALID_FROM),
    Cap(CAP_CATEGORY_CODES["R02"], Decimal("2700.00"), POLICY_VALID_FROM),
    Cap(CAP_CATEGORY_CODES["R03"], Decimal("9392.00"), POLICY_VALID_FROM),
    Cap(CAP_CATEGORY_CODES["R04"], Decimal("8700.00"), POLICY_VALID_FROM),
    # Preserve the published amount; 25% of today's lowest quota differs.
    Cap(CAP_CATEGORY_CODES["R05"], Decimal("7697.17"), POLICY_VALID_FROM),
    Cap(CAP_CATEGORY_CODES["R06"], Decimal("4148.80"), POLICY_VALID_FROM),
)

COTAS_UF = tuple(
    CotaUF(uf, Decimal(value), POLICY_VALID_FROM)
    for uf, value in (
        ("AC", "57359.87"),
        ("AL", "53164.36"),
        ("AM", "56151.46"),
        ("AP", "55929.26"),
        ("BA", "50965.29"),
        ("CE", "54879.34"),
        ("DF", "41612.55"),
        ("ES", "49160.15"),
        ("GO", "46979.73"),
        ("MA", "54537.99"),
        ("MG", "47645.91"),
        ("MS", "52707.93"),
        ("MT", "51439.83"),
        ("PA", "54624.17"),
        ("PB", "54402.48"),
        ("PE", "53997.81"),
        ("PI", "53195.84"),
        ("PR", "50807.19"),
        ("RJ", "47267.41"),
        ("RN", "55198.09"),
        ("RO", "56267.90"),
        ("RR", "58474.70"),
        ("RS", "53086.78"),
        ("SC", "51951.42"),
        ("SE", "52248.86"),
        ("SP", "48727.46"),
        ("TO", "51525.80"),
    )
)

# These additions are alternatives: holding several roles does not sum them.
ADICIONAIS = (
    tuple(
        Adicional(role, Decimal("1353.04"), POLICY_VALID_FROM)
        for role in (
            "lider_partido",
            "vice_lider_partido",
            "lider_bloco",
            "vice_lider_bloco",
            "lider_governo_camara",
            "lider_governo_congresso",
            "lider_minoria",
            "presidente_comissao_permanente",
            "vice_presidente_comissao_permanente",
            "representante_partido_menos_um_centesimo",
        )
    )
    + tuple(
        Adicional(role, Decimal("902.02"), POLICY_VALID_FROM)
        for role in (
            "vice_lider_minoria",
            "vice_lider_governo_camara",
            "vice_lider_governo_congresso",
        )
    )
    + (Adicional("suplente_secretario_mesa", Decimal("5075.62"), POLICY_VALID_FROM),)
)

# The Mercosul addition is per official trip, not a monthly role allowance.
# Trip evidence is unavailable here, so it is not added to the monthly policy data.

"""The single ordered registry of implemented deterministic rules."""

from opsiu.domain.rules.base import Rule
from opsiu.domain.rules.r01_locacao_veiculos import R01LocacaoVeiculos
from opsiu.domain.rules.r02_taxi_pedagio_estacionamento import (
    R02TaxiPedagioEstacionamento,
)
from opsiu.domain.rules.r03_combustiveis_lubrificantes import (
    R03CombustiveisLubrificantes,
)
from opsiu.domain.rules.r04_seguranca import R04Seguranca
from opsiu.domain.rules.r05_cursos_eventos import R05CursosEventos
from opsiu.domain.rules.r06_auxilio_moradia import R06AuxilioMoradia
from opsiu.domain.rules.r07_cota_uf import R07CotaUF
from opsiu.domain.rules.r14_liderancas import R14Liderancas

RULES: tuple[type[Rule], ...] = (
    R01LocacaoVeiculos,
    R02TaxiPedagioEstacionamento,
    R03CombustiveisLubrificantes,
    R04Seguranca,
    R05CursosEventos,
    R06AuxilioMoradia,
    R07CotaUF,
    R14Liderancas,
)


def get_rule(id: str) -> Rule:
    matches = [rule for rule in RULES if rule.descriptor.id == id]
    if len(matches) != 1:
        raise KeyError(f"unknown or duplicated rule: {id}")
    return matches[0]()

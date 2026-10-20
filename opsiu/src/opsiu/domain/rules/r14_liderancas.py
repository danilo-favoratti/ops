"""R14 is suspended until the pooled leadership entitlements are known."""

from opsiu.domain.rules.base import RuleDescriptor, SuspendedRule


class R14Liderancas(SuspendedRule):
    descriptor = RuleDescriptor(
        id="R14",
        version="2",
        title="Limite da cota coletiva de liderança",
        severity="revisar",
        fundamento=(
            "A cota da liderança reúne adicionais cedidos por líderes e "
            "vice-líderes, em conta administrada separadamente."
        ),
        suspended_reason=(
            "Falta o histórico das cessões e dos ajustes da cota coletiva. Um "
            "adicional individual não representa o orçamento da liderança. "
            "R14 está suspensa até existir evidência do limite aplicável."
        ),
    )

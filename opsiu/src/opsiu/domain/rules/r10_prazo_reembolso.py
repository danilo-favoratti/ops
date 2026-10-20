"""R10 is suspended until actual supply and submission dates are available."""

from opsiu.domain.rules.base import RuleDescriptor, SuspendedRule


class R10PrazoReembolso(SuspendedRule):
    descriptor = RuleDescriptor(
        id="R10",
        version="2",
        title="Prazo de apresentação da documentação",
        severity="revisar",
        fundamento=(
            "A documentação deve ser apresentada até 90 dias após o fornecimento "
            "do produto ou serviço."
        ),
        suspended_reason=(
            "A fonte CEAP não informa a data efetiva de apresentação nem comprova "
            "a data de fornecimento. Competência e emissão não substituem esses "
            "dados. R10 está suspensa até existir evidência dessas datas."
        ),
    )

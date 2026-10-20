---
id: R14
version: "2"
severidade: revisar
fundamento: "A cota da liderança reúne adicionais cedidos por líderes e vice-líderes, em conta administrada separadamente."
---

# R14 — Limite da cota coletiva de liderança

## Fundamento e limitação dos dados

O [Ato da Mesa 43, art. 14](https://www2.camara.leg.br/legin/int/atomes/2009/atodamesa-43-21-maio-2009-588364-normaatualizada-cd-mesa.html)
prevê cessões de adicionais para a conta coletiva e ajustes conforme o exercício
dos cargos. Sem o histórico dessas cessões e ajustes, o orçamento da conta
não está estabelecido.

A versão 1 comparava toda a liderança com apenas um adicional individual.
Reconhecer o nome da conta não resolve essa diferença. A versão 2 suspende
a geração de achados até existir evidência do orçamento coletivo aplicável.

## Comportamento determinístico

`check --regras R14` registra o motivo no evento `rule.suspended`, marca a
etapa como ignorada e não aplica o cálculo de cota individual. Em execução
normal, acrescenta supersessões para os achados anteriores da regra, mantendo
seus registros, decisões humanas, pareceres e rastros. Não cria rejeições
humanas automáticas nem certifica as despesas.

`--dry-run` não altera o histórico. A margem de R07 não reativa R14 nem fornece
um orçamento ausente. Reexecutar a suspensão não duplica as supersessões.

## Condições para reativação

Uma nova versão precisa identificar a liderança e o período, cada cessão e
ajuste, o saldo transportado dentro do exercício e a proveniência dos registros.
Ausência de cessão informada não pode ser interpretada como orçamento zero.
A publicação de resultados exige cobertura completa das contas avaliadas.

## Orientação para revisão e agentes

Os valores de achados antigos podem repetir posições cumulativas. Sua soma
não representa despesa única ou prejuízo. A suspensão corrige um fundamento
insuficiente; não demonstra que todas as despesas eram permitidas. Qualquer
reativação exige nova versão e novos testes, sem sobrescrever o histórico.

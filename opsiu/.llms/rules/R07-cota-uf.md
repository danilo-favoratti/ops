---
id: R07
version: "1"
severidade: media
fundamento: "O saldo mensal não utilizado em um mês acumula-se ao longo do exercício financeiro, vedada a acumulação de um exercício financeiro para o seguinte."
---

# R07 — Cota por UF com saldo acumulado e margem

## Fundamento e alcance

A [fonte](../../cota-parlamentar.md) estabelece cotas mensais distintas por UF
e permite acumular saldo dentro do exercício financeiro. O controle compara o
gasto líquido acumulado com a dotação acumulada daquele ano, incluindo os meses
sem registros de despesas.

Este é um controle aproximado, de severidade média. Os valores publicados têm
vigência inicial assumida em `2023-01-01`, conforme o
[ADR 0008](../../docs/adr/0008-policy-effective-date-assumption.md). Essa data não
comprova a vigência histórica de cada valor. A fonte de despesas também não
fornece cargos e nomeações históricas dos parlamentares regulares: esta versão
usa apenas a cota da UF e não presume adicionais individuais. Um adicional
documentado durante a revisão pode explicar parte do excesso sinalizado.

## Campos

Usar os registros correntes de `despesas_current`, com `numero_deputado_id`,
`ano`, `mes`, `valor_liquido` e `id_documento`. Derivar UF e classificação de
liderança dos próprios registros de cada parlamentar e ano, usando `sigla_uf`
e `cpf`. UF ou classificação conflitante no mesmo ano causa falha explícita.
R07 considera os parlamentares regulares; as contas classificadas como
liderança pertencem à R14.

A dotação mensal vem de `cota_uf`, com seleção pela UF e pela maior `valid_from`
que não ultrapasse o primeiro dia de cada competência. Ausência de política
aplicável é uma falha registrada da regra, sem publicação parcial de achados.

## Lógica determinística

Para cada parlamentar e ano, montar o calendário de janeiro até o último mês
com despesas. Somar as despesas líquidas de todas as categorias por mês e
preencher meses sem registros com gasto zero. Selecionar a cota vigente em cada
mês e somar essas dotações; uma mudança de valor não é aplicada retroativamente
aos meses anteriores.

Definir:

- `gasto acumulado`: soma dos gastos líquidos desde janeiro até o mês avaliado.
- `dotação acumulada`: soma das cotas mensais vigentes no mesmo intervalo.
- `disponível antes do mês`: dotação acumulada menos os gastos dos meses
  anteriores, antes de descontar o gasto do mês corrente.
- `limiar com margem`: dotação acumulada multiplicada por
  `1 + quota_margin_percent / 100`, sem arredondamento intermediário.

Emitir achado somente em mês com registros de despesas e quando o gasto
acumulado for estritamente maior que o limiar com margem. A igualdade não
dispara. Associar o achado ao maior `id_documento` daquele mês. Reiniciar as
somas em janeiro de cada ano; saldo de dezembro não passa ao ano seguinte.

O valor do achado é o excesso sobre a dotação acumulada original, antes da
margem. A margem filtra a emissão do achado e não reduz esse valor. Excedentes
de meses consecutivos se sobrepõem: somá-los não produz um excesso único do ano.

`rules.quota_margin_percent` aceita um Decimal finito e não negativo, com
padrão `5`. Pode ser substituído pela variável
`OPSIU_RULES__QUOTA_MARGIN_PERCENT`. A configuração usada fica identificada na
execução; margem zero desativa apenas a tolerância percentual.

Alterar a margem modifica a evidência sem alterar a chave formada por regra,
versão e documento. Uma nova verificação sobre achados existentes pode falhar
por conflito de imutabilidade, conforme o
[ADR 0009](../../docs/adr/0009-immutable-finding-conflicts.md). Achados e revisões
anteriores devem ser preservados, sem sobrescrita silenciosa.

## Evidência

Registrar a identificação em `numero_deputado_id`, `ano`, `mes`, `uf`, `cargo`
e `lideranca`; os valores em `gasto_mensal`, `gasto_acumulado`,
`limite_acumulado`, `cota_mensal` e `adicional_mensal`; e a lista ordenada
`documentos` do mês. Nesta regra, o adicional mensal é zero.

A evidência também contém `approximate = true`, `margem_percentual`,
`limite_com_margem`, `disponivel_no_mes` e
`cargos_individuais_disponiveis = false`. Este último campo explicita a falta
de histórico dos cargos individuais. `disponivel_no_mes` é o valor disponível
antes de descontar o gasto do mês corrente.

`serie_mensal` inclui meses de gasto zero. Cada elemento contém `mes`, `gasto`,
`cota`, `adicional`, `limite` e `vigente_desde`. O documento associado mantém a
proveniência de arquivo, registro, execução, etapa e versão da regra.

## Exemplos sintéticos

Na UF DF, a cota mensal do exemplo é R$ 41.612,55. Com margem de 5%, o limiar
de janeiro é exatamente R$ 43.693,1775.

- O parlamentar `101` gasta R$ 45.351,03 em janeiro: excesso sobre a dotação
  de R$ 3.738,48, associado a `209900032`. Fevereiro e março também disparam,
  associados a `209900034` e `209900036`, com valores acumulados sobrepostos.
- O parlamentar `501` gasta R$ 1.000,00 em janeiro e R$ 70.000,00 em fevereiro.
  O total de R$ 71.000,00 cabe nas duas cotas de R$ 83.225,10; o mês alto é
  coberto pelo saldo anterior e não dispara.
- Os parlamentares `502` e `503` gastam, respectivamente, R$ 42.000,00 e
  R$ 43.693,17 em janeiro. A margem padrão suprime ambos os achados. Com margem
  zero, disparam os documentos `209900053` e `209900054`.
- O parlamentar `504` gasta R$ 43.693,18 em janeiro, acima do limiar sem
  arredondamento: gera achado de R$ 2.080,63 em `209900055`.

Assim, os documentos positivos da amostra com margem padrão são `209900032`,
`209900034`, `209900036` e `209900055`. Os valores são exclusivamente sintéticos.

## O que o agente deve verificar

Confira UF, competências, documentos, glosas, série mensal e políticas citadas.
Verifique se as evidências disponíveis esclarecem algum adicional individual,
período de exercício ou mudança histórica de valor. Registre a informação
ausente e o efeito possível sobre a interpretação, sem inventar direito a saldo
ou adicional. Preserve a natureza aproximada do controle e não some achados
acumulados como despesas independentes.

O agente anota o resultado determinístico e suas limitações; não decide se a
regra dispara. A conclusão para o relatório final depende da revisão humana.

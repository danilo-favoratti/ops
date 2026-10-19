---
id: R09
version: "1"
severidade: media
fundamento: "O parlamentar assume inteira responsabilidade pela nota fiscal que apresenta (art. 4º do Ato da Mesa 43/2009)."
---

# R09 — Possível duplicidade de nota fiscal

## Fundamento e alcance

A [fonte](../../cota-parlamentar.md) atribui ao parlamentar responsabilidade
pela nota apresentada. Ela não contém uma cláusula específica sobre a combinação
de campos usada para detectar duplicidade. O trecho do cabeçalho contextualiza
a responsabilidade documental; não constitui uma proibição textual inventada.

Esta regra de severidade média sinaliza possíveis duplicidades para revisão.
Coincidência de favorecido, número e valor bruto não comprova que todos os
registros representam pagamentos indevidos ou valores recuperáveis.

## Campos e lógica determinística

Usar `despesas_current` e agrupar por `cnpj_cpf_digits`, `numero` e
`valor_documento`. O identificador do favorecido já está normalizado para
dígitos. O número da nota é comparado como texto exato após a normalização da
ingestão, preservando zeros e diferenças de conteúdo. O valor de comparação é
o bruto do documento, sem descontar glosas.

Excluir registros sem favorecido, número ou valor bruto. Um grupo só dispara
quando contiver mais de um `id_documento` distinto, mesmo que os documentos
pertençam a parlamentares diferentes. Repetições do mesmo identificador, sem
outro documento distinto, não bastam para caracterizar o grupo.

Gerar um achado para cada documento do grupo, com a lista ordenada dos
identificadores distintos relacionados. O valor de cada achado é o valor bruto da nota.
Essa repetição permite revisar cada registro e não autoriza somar os achados
como um total recuperável: eles descrevem a mesma suspeita de duplicidade.

A proveniência de cada achado aponta para o seu documento, arquivo, registro,
execução, etapa e versão da regra.

## Evidência

Os campos são `cnpj_cpf_digits`, `numero`, `valor_documento`, `documentos`
e `possivel_duplicidade = true`. `documentos` contém identificadores distintos
em ordem crescente; o valor bruto é serializado como texto decimal exato.
Os documentos associados fornecem o contexto de cada parlamentar. Diferenças
de valor líquido não alteram o critério de agrupamento.

## Exemplos sintéticos

Os documentos `209900045` e `209900046` usam o mesmo favorecido sintético,
número `R09-DUPLICADA` e valor bruto de R$ 321,09. Pertencem, respectivamente,
aos parlamentares `301` e `302`:

- Ambos recebem achado, com valor individual de R$ 321,09 e a lista ordenada
  `[209900045, 209900046]`.
- O segundo documento tem glosa de R$ 1,00 e valor líquido de R$ 320,09.
  Essa diferença não rompe a coincidência de valor bruto.
- `209900047` tem o mesmo favorecido e valor bruto, mas número
  `R09-NUMERO-DIFERENTE`; não integra o grupo e não dispara.

Dois achados de R$ 321,09 não comprovam um valor recuperável de R$ 642,18.

## O que o agente deve verificar

Compare os documentos do grupo, os emitentes, os números, as datas e os valores
brutos e líquidos. Registre se há documentos diferentes, correções, estornos,
rateios ou outras explicações efetivamente visíveis nas evidências, sem
presumi-los quando faltarem comprovantes. Indique quais documentos sustentam
cada observação e quais dúvidas permanecem.

O agente produz uma anotação sobre o agrupamento determinístico; não declara
por conta própria a existência de reembolso duplicado ou o montante recuperável.
A conclusão para o relatório final depende da revisão humana.

---
id: R05
version: "1"
severidade: alta
fundamento: "participação em cursos, congressos ou eventos, realizados por instituição especializada (limite mensal inacumulável de 25% do valor da menor cota – hoje R$ 7.697,17);"
---

# R05 — Cursos, congressos e eventos

## Fundamento

A fonte é [cota-parlamentar.md](../../cota-parlamentar.md), na seção de despesas
permitidas. O trecho literal está no campo `fundamento` do cabeçalho. O limite
mensal inicial é R$ 7.697,17, inacumulável: saldo de outro mês não aumenta
este teto.

A vigência inicial em `2023-01-01` é uma hipótese operacional, descrita no
[ADR 0008](../../docs/adr/0008-policy-effective-date-assumption.md), e não uma
reconstrução das datas históricas de alteração dos valores.

Embora o fundamento mencione 25% da menor cota, esta versão preserva o valor publicado
de R$ 7.697,17. Não se recalcula o limite a partir da tabela atual de cotas por UF; a
escolha está documentada no ADR 0008.

## Campos

Usar `despesas_current`, que contém a versão corrente de cada arquivo anual:
`numero_deputado_id`, `ano`, `mes`, `numero_sub_cota`, `valor_liquido` e
`id_documento`. A categoria é `137`, correspondente a `PARTICIPAÇÃO EM CURSO, PALESTRA
OU EVENTO SIMILAR`.
O limite vem de `caps`, pelos campos `category_code`, `limit` e `valid_from`.

## Lógica determinística

Agrupar os registros da categoria por parlamentar, ano e mês e somar
`valor_liquido`. Selecionar a política da categoria com a maior `valid_from`
que não ultrapasse o primeiro dia da competência. Ausência de política vigente
é uma falha registrada da regra; o grupo não pode desaparecer silenciosamente.

Emitir um achado somente quando `total > limite`. A igualdade não dispara.
O valor do achado é `total - limite`, em centavos exatos. Associar o achado ao
maior `id_documento` do grupo e guardar a lista ordenada de todos os documentos.
Não aplicar saldo acumulado ou margem percentual a este limite mensal.

A evidência contém `total`, `limite`, `documentos`, `numero_deputado_id`, `ano`,
`mes`, `numero_sub_cota` e `vigente_desde`. A proveniência liga o documento
associado ao arquivo, registro, execução, etapa e versão da regra.

## Exemplos sintéticos

No arquivo de testes `Ano-2099.json`, o parlamentar `101` tem dois documentos
por competência nesta categoria:

- Janeiro: soma R$ 7.697,18; gera um achado de R$ 0,01,
  associado ao documento `209900026`.
- Fevereiro: soma R$ 7.697,16; não gera achado.
- Março: soma R$ 7.697,17; igualdade com o limite, sem achado.

Cada documento isolado está abaixo do teto; o exemplo positivo depende da soma.
Os valores foram criados para testes, sem afirmação sobre despesas reais.

## O que o agente deve verificar

Confira a instituição, o evento, as datas, o participante identificado e os valores de
inscrição ou participação. Aponte informações ausentes e eventuais divergências de
descrição, sem inferir frequência ou qualificação da instituição.

Confira a correspondência entre os documentos citados e a evidência agregada,
respeitando valores líquidos e glosas. Registre limitações de leitura ou ausência
de comprovantes. O agente produz uma anotação fundamentada; a decisão de disparar
a regra pertence ao código determinístico, e a aprovação final cabe à revisão
humana.

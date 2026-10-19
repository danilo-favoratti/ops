---
id: R01
version: "2"
severidade: alta
fundamento: "locação ou fretamento de veículos automotores (limite inacumulável de R$ 12.713,00 mensais), permitida contratação de seguro;"
---

# R01 — Locação de veículos automotores

## Fundamento

A fonte é [cota-parlamentar.md](../../cota-parlamentar.md), na seção de despesas
permitidas. O trecho literal está no campo `fundamento` do cabeçalho. O limite
mensal inicial é R$ 12.713,00, inacumulável: saldo de outro mês não aumenta
este teto.

A vigência inicial em `2023-01-01` é uma hipótese operacional, descrita no
[ADR 0008](../../docs/adr/0008-policy-effective-date-assumption.md), e não uma
reconstrução das datas históricas de alteração dos valores.

## Campos

Usar `despesas_current`, que contém a versão corrente de cada arquivo anual:
`numero_deputado_id`, `ano`, `mes`, `numero_sub_cota`, `valor_liquido` e
`id_documento`. A categoria é `120`, correspondente a `LOCAÇÃO OU FRETAMENTO DE VEÍCULOS
AUTOMOTORES`.
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

- Janeiro: soma R$ 12.713,01; gera um achado de R$ 0,01,
  associado ao documento `209900002`.
- Fevereiro: soma R$ 12.712,99; não gera achado.
- Março: soma R$ 12.713,00; igualdade com o limite, sem achado.

Cada documento isolado está abaixo do teto; o exemplo positivo depende da soma.
Os valores foram criados para testes, sem afirmação sobre despesas reais.

## O que o agente deve verificar

Confira a identificação do veículo, o período contratado, os valores e eventuais itens
de seguro descritos nos comprovantes. Registre separadamente dúvidas de classificação ou
divergências entre contrato e nota, citando o documento.

Confira a correspondência entre os documentos citados e a evidência agregada,
respeitando valores líquidos e glosas. Registre limitações de leitura ou ausência
de comprovantes. O agente produz uma anotação fundamentada; a decisão de disparar
a regra pertence ao código determinístico, e a aprovação final cabe à revisão
humana.

## Registros contábeis e identidade (versão 2)

Um documento pode ocorrer em várias linhas da fonte. Manter um achado por
regra, versão e documento, preservando todas as ocorrências avaliadas em
`ocorrencias` e os arquivos e hashes em `registros_origem` quando houver
multiplicidade. O valor agregado soma os valores dessas ocorrências; não
representa automaticamente um valor recuperável. A proveniência principal
é uma âncora determinística ordenada por arquivo e hash do registro.

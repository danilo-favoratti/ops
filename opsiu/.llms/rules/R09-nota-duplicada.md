---
id: R09
version: "3"
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
Coincidência de favorecido, número, valor bruto e data de emissão não comprova que todos os
registros representam pagamentos indevidos ou valores recuperáveis.

## Campos e lógica determinística

Usar `despesas_current` e agrupar por `cnpj_cpf_digits`, `numero`,
`valor_documento` e `data_emissao`. O identificador do favorecido já está normalizado para
dígitos. O número da nota é comparado como texto exato após a normalização da
ingestão, preservando zeros e diferenças de conteúdo. O valor de comparação é
o bruto do documento, sem descontar glosas.

Excluir registros sem favorecido, número, valor bruto ou data de emissão conhecida,
e documentos com identificador zero ou negativo. Excluir marcadores de ausência
de número: `SN`, `SNO`, `ND`, `SEMNUMERO`, conteúdo apenas de pontuação ou
apenas de zeros. Para reconhecer esses marcadores, ignorar acentos, caixa,
espaços e pontuação (por exemplo, `s/n`, `s/nº`, `n/d` e `sem número`). Essa
normalização serve somente à exclusão; não altera os números reais usados no
agrupamento. Assim, `000001` e `1` continuam diferentes, e `SN123` continua válido.

Exigir a mesma data de emissão conhecida evita que números reutilizados em
dias diferentes sejam tratados como a mesma nota apenas pelos metadados.
Ausência do achado não comprova regularidade: documentos com metadados diferentes
podem exigir outras evidências para revisão.

Um grupo só dispara
quando contiver mais de um `id_documento` distinto, mesmo que os documentos
pertençam a parlamentares diferentes. Repetições do mesmo identificador, sem
outro documento distinto, não bastam para caracterizar o grupo. Diferenças no
tipo de documento não excluem uma coincidência que satisfaça os demais critérios.

Gerar um achado para cada documento do grupo, com a lista ordenada dos
identificadores distintos relacionados. O valor de cada achado é o valor bruto da nota.
Essa repetição permite revisar cada registro e não autoriza somar os achados
como um total recuperável: eles descrevem a mesma suspeita de duplicidade.

A proveniência de cada achado aponta para o seu documento, arquivo, registro,
execução, etapa e versão da regra.

## Evidência

Os campos são `cnpj_cpf_digits`, `numero`, `valor_documento`, `data_emissao`,
`documentos`, `possivel_duplicidade = true`, `criterio` e `limitacoes`.
O critério é `fornecedor_numero_valor_bruto_data_emissao`; as limitações explicam
que metadados coincidentes não comprovam pagamento duplicado e que é necessário
conferir comprovantes, estornos, glosas e rateios. `documentos` contém identificadores distintos
em ordem crescente; o valor bruto é serializado como texto decimal exato.
Os documentos associados fornecem o contexto de cada parlamentar. Diferenças
de valor líquido não alteram o critério de agrupamento.

## Exemplos sintéticos

Os documentos `209900045` e `209900046` usam o mesmo favorecido sintético,
número `R09-DUPLICADA`, valor bruto de R$ 321,09 e a mesma data de emissão. Pertencem, respectivamente,
aos parlamentares `301` e `302`:

- Ambos recebem achado, com valor individual de R$ 321,09 e a lista ordenada
  `[209900045, 209900046]`.
- O segundo documento tem glosa de R$ 1,00 e valor líquido de R$ 320,09.
  Essa diferença não rompe a coincidência de valor bruto.
- `209900047` tem o mesmo favorecido e valor bruto, mas número
  `R09-NUMERO-DIFERENTE`; não integra o grupo e não dispara.
- Um número reutilizado em outra data não integra o grupo. Duas despesas com
  número `s/n`, mesmo emitente, valor e data também não disparam por esses
  metadados: falta um número de nota utilizável.

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

## Registros contábeis e identidade (versão 2)

Um documento pode ocorrer em várias linhas da fonte. Manter um achado por
regra, versão e documento, preservando todas as ocorrências avaliadas em
`ocorrencias` e os arquivos e hashes em `registros_origem` quando houver
multiplicidade. O valor agregado soma os valores dessas ocorrências; não
representa automaticamente um valor recuperável. A proveniência principal
é uma âncora determinística ordenada por arquivo e hash do registro.


## Comparação de comprovantes em cache (versão 3)

`check --regras R09` também compara capturas locais ligadas às URLs correntes.
Verificar tamanho e SHA-256 contra a captura imutável; somente arquivos com
mesmo tamanho e hash são comparados byte a byte em blocos. Igualdade de tamanho
sozinha, texto extraído igual ou ausência de OCR não comprovam igualdade binária.
Falha de integridade impede a publicação desta checagem.

Arquivos byte a byte idênticos em IDs positivos distintos geram um indício mesmo
quando número, fornecedor ou data não coincidirem. A evidência conserva URLs,
caminhos, tamanhos, hashes e identificadores de snapshot. O critério binário é
complementar ao critério de metadados. Quando ambos disparam para um documento,
o achado conserva apenas o critério de metadados; a comparação binária continua
registrada nos eventos da execução. Isso evita alterar evidência imutável quando
o cache cresce. Achados binários existentes conservam sua evidência inicial
enquanto os comprovantes nela citados continuarem verificados no grupo atual. Bytes idênticos não comprovam reembolso em duplicidade:
conferir parcelas, estornos, restituições, glosas e eventual rateio.

A comparação automática é limitada ao cache existente (até 2.000 referências e
256 MiB por checagem); não presume que os demais documentos foram examinados.
`receipts fetch --limit 20` captura uma amostra limitada de grupos completos
candidatos, preservando cache e proveniência, antes de nova checagem. Ausência
de arquivo idêntico no cache não prova que comprovantes distintos foram enviados.

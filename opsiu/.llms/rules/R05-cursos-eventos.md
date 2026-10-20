---
id: R05
version: "2"
severidade: alta
fundamento: "Cursos e eventos especializados: limite mensal inacumulável de 25% da menor cota mensal, conforme o art. 2º, XIII, do Ato da Mesa 43/2009; aplicar a tabela vigente na competência."
---

# R05 — Cursos, congressos e eventos

## Fundamento e vigência

O [art. 2º, XIII, do Ato da Mesa 43/2009](https://www2.camara.leg.br/legin/int/atomes/2009/atodamesa-43-21-maio-2009-588364-normaatualizada-cd-mesa.html)
estabelece um limite mensal de 25% da menor cota. A versão 2 substitui o valor
fixo antigo por períodos pesquisados, sem usar a tabela atual retroativamente.

- Janeiro de 2023: menor cota de R$ 30.788,66, do DF, no
  [Ato da Mesa 76/2016](https://www2.camara.leg.br/legin/int/atomes/2016/atodamesa-76-3-fevereiro-2016-782328-publicacaooriginal-149360-cd-mesa.html),
  vigente desde 01/03/2016. Limite calculado: R$ 7.697,17.
- Fevereiro de 2023 a janeiro de 2026: menor cota de R$ 36.582,46, do DF,
  no [Anexo I do Ato da Mesa 270/2023](https://www2.camara.leg.br/legin/int/atomes/2023/atodamesa-270-19-janeiro-2023-793708-anexo-cd-mesa.pdf).
  O [ato](https://www2.camara.leg.br/legin/int/atomes/2023/atodamesa-270-19-janeiro-2023-793708-publicacaooriginal-166895-cd-mesa.html)
  entrou em vigor em 01/02/2023. Limite: R$ 9.145,62.
- Fevereiro de 2026: revisão de transição. O
  [Ato da Mesa 244/2026](https://www2.camara.leg.br/legin/int/atomes/2026/atodamesa-244-20-fevereiro-2026-798720-publicacaooriginal-178157-cd-mesa.html)
  entrou em vigor em 20/02/2026. A competência mensal não resolve a aplicação
  temporal do reajuste. Diferenças para R$ 9.145,62 recebem `revisar`,
  registrando também o novo limite de R$ 10.403,14.
- Março a dezembro de 2026: menor cota publicada de R$ 41.612,55, do DF,
  na [tabela da Câmara](https://www2.camara.leg.br/comunicacao/assessoria-de-imprensa/guia-para-jornalistas/cota-parlamentar).
  Limite: R$ 10.403,14. O valor da cota é o publicado pela Câmara; a regra
  não recalcula a cota a partir de uma razão de índices sem arredondamento.

O cálculo `menor cota × 0.25` usa `ROUND_HALF_UP` para centavos, convenção
computacional explícita. Assim, `9145.615` resulta em R$ 9.145,62, sem criar
um excesso de meio centavo. Não há margem percentual nem saldo acumulado.

## Campos e lógica determinística

Agrupar `despesas_current` por parlamentar, ano e mês, na categoria `137`
(`PARTICIPAÇÃO EM CURSO, PALESTRA OU EVENTO SIMILAR`). Somar `valor_liquido`,
inclusive linhas negativas. Emitir quando a soma for maior que o limite
pesquisado e associar ao maior `id_documento`, preservando a lista completa.

Os períodos imutáveis estão em `domain/researched_caps.py`; as antigas linhas
de `caps` e `cota_uf` permanecem intactas para preservar o histórico.
Esta versão cobre 2023–2026. Fora desse intervalo, informar a lacuna e impedir
a publicação completa da regra; não tratar ausência de política como ausência
de excesso nem superseder achados anteriores.

A evidência registra total, limite, documentos, competência, vigência legal,
intervalo pesquisado, fórmula, arredondamento, fontes e limitação de transição.
Documentos em vários grupos preservam todas as `ocorrencias` e os
`registros_origem`, com uma identidade de achado por regra, versão e documento.

## Exemplos

- Janeiro de 2023, R$ 7.697,18: diferença de R$ 0,01.
- Fevereiro de 2023, R$ 9.145,62: nenhum achado; R$ 9.145,63: R$ 0,01.
- Fevereiro de 2026, R$ 10.000,00: revisão de transição.
- Março de 2026, R$ 10.403,14: nenhum achado.

## O que o agente deve verificar

Conferir instituição, atividade, participação, competência, valores líquidos e
glosas. Diferenciar evento especializado de graduação ou pós-graduação, sem
tirar conclusão apenas pelo nome do fornecedor. Em fevereiro de 2026, conferir
a aplicação temporal do reajuste. A diferença sinalizada não é prova de prejuízo.
O agente anota as evidências; a decisão de revisão cabe ao revisor humano.

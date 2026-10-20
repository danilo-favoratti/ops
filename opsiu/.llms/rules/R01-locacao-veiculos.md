---
id: R01
version: "3"
severidade: alta
fundamento: "Locação de veículos: limite mensal inacumulável, com atualização pelo IPCA determinada pelo Ato da Mesa 196/2025 e previsão adicional do Ato da Mesa 246/2026; aplicar a política da competência."
---

# R01 — Locação de veículos automotores

## Fundamento e vigência

O [Ato da Mesa 183/2017](https://www2.camara.leg.br/legin/int/atomes/2017/atodamesa-183-16-maio-2017-784814-publicacaooriginal-152684-cd-mesa.html)
fixou o limite mensal em R$ 12.713,00, vigente desde 17/05/2017.
O [Ato da Mesa 196/2025, art. 4º](https://www2.camara.leg.br/legin/int/atomes/2025/atodamesa-196-27-maio-2025-797512-publicacaooriginal-175496-cd-mesa.html)
determinou sua atualização pelo IPCA de maio de 2017 a fevereiro de 2025.
A publicação ocorreu em 28/05/2025.

O cálculo usa o [número-índice oficial do IBGE](https://apisidra.ibge.gov.br/values/t/1737/n1/all/v/2266/p/201704,202502):
`12713.00 × 7205.03 / 4828.44`, arredondado para centavos com
`ROUND_HALF_UP`, resulta em **R$ 18.970,42**. Abril de 2017 é o denominador
porque a variação inclui maio. O arredondamento é uma convenção computacional
explícita; diferenças de centavos continuam visíveis.

A versão 3 contém períodos pesquisados exclusivamente para 2023–2026:

- Janeiro de 2023 a abril de 2025: R$ 12.713,00.
- Maio de 2025: mês de transição; diferenças para R$ 12.713,00 recebem
  `revisar`, com R$ 18.970,42 registrado como novo limite possível.
- Junho de 2025 a fevereiro de 2026: R$ 18.970,42.
- Março a dezembro de 2026: referência básica de R$ 18.970,42; diferenças
  recebem `revisar`, porque o
  [Ato da Mesa 246/2026](https://www2.camara.leg.br/legin/int/atomes/2026/atodamesa-246-2-marco-2026-798777-publicacaooriginal-178340-cd-mesa.html),
  publicado em 03/03/2026, introduziu previsão adicional vinculada ao art. 2º,
  §§ 3º e 5º. O conjunto de dados não informa a autorização nem o saldo
  individual aplicável. Março também é mês de transição.

Não aplicar o limite antigo como excesso comprovado em meses de transição.
Não presumir autorização individual para o adicional de 2026. Um achado de
revisão informa diferença para uma referência, sem certificar irregularidade.

## Campos e lógica determinística

Agrupar `despesas_current` por `numero_deputado_id`, `ano`, `mes` e categoria
`120` (`LOCAÇÃO OU FRETAMENTO DE VEÍCULOS AUTOMOTORES`), somando
`valor_liquido`, inclusive linhas negativas. Comparar com o período pesquisado
da competência. A igualdade não dispara; a diferença positiva forma o valor
sinalizado, sem representar automaticamente prejuízo ou restituição devida.

O catálogo imutável de `domain/researched_caps.py` é independente das linhas
históricas de `caps`. A versão 3 não reescreve essas linhas nem aplica a hipótese
antiga do ADR 0008. Competências fora da cobertura são informadas como lacunas;
a execução não publica um resultado completo nem supersede achados anteriores
da regra enquanto houver lacuna.

Associar ao maior `id_documento`, preservar todos os documentos e incluir:
total, limite, parlamentar, competência, categoria, vigência legal,
intervalo pesquisado, fontes, fórmula, arredondamento e limitações.
Quando um documento aparece em vários grupos, preservar `ocorrencias` e
`registros_origem`. O valor combinado soma as diferenças dessas ocorrências.

## Exemplos

- Abril de 2025, R$ 12.713,01: diferença de R$ 0,01.
- Junho de 2025, R$ 18.970,42: nenhum achado; R$ 18.970,43: R$ 0,01.
- Maio de 2025, R$ 18.000,00: revisão de transição, sem afirmar excesso.
- Abril de 2026, R$ 19.000,00: revisão da autorização e do adicional.

## O que o agente deve verificar

Conferir veículos, período contratado, seguros, valores líquidos e glosas.
Nos meses de transição, verificar os documentos e a aplicação temporal da norma.
A partir de março de 2026, buscar a autorização e o saldo do adicional.
Registrar restituições como recuperação, preservando o contexto do gasto.
Não inferir prejuízo a partir da diferença calculada.
O agente apenas anota; a regra é determinística e a decisão final é humana.

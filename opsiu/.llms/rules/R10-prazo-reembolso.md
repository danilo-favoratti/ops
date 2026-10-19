---
id: R10
version: "1"
severidade: media
fundamento: "O deputado tem até 90 dias para apresentar a documentação comprobatória do gasto."
---

# R10 — Intervalo entre emissão e competência superior a 90 dias

## Fundamento e alcance

A [fonte](../../cota-parlamentar.md) estabelece prazo de até 90 dias para
apresentação da documentação. Os dados usados aqui informam a data de emissão
e a competência mensal, mas não a data efetiva de apresentação.

Por isso, esta regra de severidade média usa o primeiro dia da competência
como aproximação para comparar o intervalo. O resultado é um sinal para
revisão documental; não comprova, sozinho, apresentação fora do prazo legal.
O primeiro dia de um mês não deve ser descrito como uma data real de protocolo.

## Campos e lógica determinística

Usar `despesas_current`, com `ano`, `mes`, `data_emissao`, `valor_liquido` e
`id_documento`, mantendo o parlamentar como contexto.

Construir a data `competencia` com o ano e o mês do registro e dia `1`.
Calcular dias corridos entre `data_emissao` e essa data. Emitir um achado apenas
quando a diferença for estritamente maior que 90. Exatamente 90 dias não
dispara; 89 dias também não.

Emissão desconhecida não permite a comparação e não dispara. Emissão
posterior ao primeiro dia da competência produz diferença negativa e também
não dispara. Esta regra não faz uma checagem independente de datas futuras.

O valor do achado é o `valor_liquido` do documento. A proveniência registra
arquivo, registro, execução, etapa e versão da regra.

## Evidência

Os campos são `data_emissao`, `competencia`, `dias_decorridos`,
`prazo_dias = 90` e `competencia_e_proxy = true`. As datas são serializadas no
formato ISO, e `competencia` representa o primeiro dia do mês. O documento
associado fornece o contexto do parlamentar e do valor líquido. O marcador
de aproximação preserva a distinção em relação à data real de apresentação.

## Exemplos sintéticos

Os três documentos do parlamentar `401` têm competência de julho de 2099,
portanto a data de comparação é `2099-07-01`:

- `209900048`, emissão `2099-04-01`: diferença de 91 dias; gera achado
  de R$ 10,00.
- `209900049`, emissão `2099-04-02`: exatamente 90 dias; não dispara.
- `209900050`, emissão `2099-04-03`: 89 dias; não dispara.

Esses exemplos verificam a fronteira do cálculo, sem estabelecer uma data
de apresentação que não consta dos registros.

## O que o agente deve verificar

Confira as datas legíveis na nota, o mês e ano registrados e o cálculo do
intervalo. Se houver comprovante de apresentação, cite sua data e sua origem
separadamente da competência. Registre retificações, diferenças de leitura
e informações ausentes apenas quando sustentadas pela evidência.

O agente não transforma a aproximação mensal em prova de atraso. Ele anota
o resultado determinístico; a conclusão e a aprovação para o relatório final
cabem à revisão humana.
